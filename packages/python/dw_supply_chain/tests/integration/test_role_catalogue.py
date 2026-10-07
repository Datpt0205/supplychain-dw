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
from supply_chain_harness import REPO_ROOT, DatabaseUrls

from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application import handlers
from dw_supply_chain.policy_files import PRODUCT_APPROVALS_POLICY_FILE
from dw_supply_chain.product_approvals import load_supply_chain_product_approvals

pytestmark = pytest.mark.integration

# The roles that run PO cases; each also raises and clears exceptions.
_PO_OPERATING_ROLES = (
    "sc_operator",
    "sc_finance",
    "sc_qc",
    "sc_logistics",
    "sc_warehouse",
)
_OPERATING_ROLES = (*_PO_OPERATING_ROLES, "sc_rnd", "sc_supply_lead")
_SC_ROLES = ("sc_viewer", *_OPERATING_ROLES, "sc_bod", "sc_process_admin")
_APPROVALS = load_supply_chain_product_approvals(
    REPO_ROOT / "configs" / "policies" / PRODUCT_APPROVALS_POLICY_FILE
)
# The scope BGĐ's review is stamped with under the platform policy (step 6).
_APPROVE_BOD = _APPROVALS.bod_review.required_scope
# And the sign-off's (step 9): BGĐ's, then Kế toán's.
_SIGNOFF_SCOPES = {step.required_scope for step in _APPROVALS.signoff}
_APPROVE_ACCOUNTING = "supply_chain.approve.accounting"
_OPERATIONS = {
    "supply_chain.po_case.write",
    "supply_chain.supplier_update.write",
    "supply_chain.delay_impact.write",
    handlers.DOCUMENT_WRITE,
    handlers.PRODUCT_CASE_WRITE,
    _APPROVE_BOD,
    *_SIGNOFF_SCOPES,
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
    duties = {handlers.duty_scope(duty) for duty in CaseDuty}
    return constants | duties | {_APPROVE_BOD} | _SIGNOFF_SCOPES


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
    assert _APPROVE_BOD == "supply_chain.approve.bod"


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
    """A QC inspector who finds the line blocked must be able to say so.
    `sc_rnd` is not among them: the exceptions duty is shared by both case
    kinds, and R&D runs no PO step (lead decision 8,
    `test_rnd_holds_exactly_the_viewer_its_duty_and_the_document_write`)."""
    catalogue = await _catalogue(engine)
    exceptions = handlers.duty_scope(CaseDuty.EXCEPTIONS)
    for key in _PO_OPERATING_ROLES:
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


async def test_the_process_admin_cannot_also_hold_any_operation(engine: AsyncEngine) -> None:
    """The rule's operations side names every operation scope, every duty
    included (the document write, and `duty.rnd` from 7c422b849fe9), so a
    future role granting one duty alone still meets the rule. Asked of the
    rule's own row: a role-pair test passes on whichever other operation
    scope the role happens to hold as well."""
    async with engine.connect() as conn:
        right = await conn.scalar(
            text("SELECT right_scopes FROM platform.sod_rules WHERE key = :k"),
            {"k": "sod_sc_rules_vs_operations"},
        )
    assert _OPERATIONS - set(right) == set()


async def test_every_role_reads_product_cases(engine: AsyncEngine) -> None:
    """Stage 1: every holder of the read sees every product case of its
    workspace; the PIC filter only narrows (QE-18)."""
    catalogue = await _catalogue(engine)
    for key in _SC_ROLES:
        assert handlers.PRODUCT_CASE_READ in catalogue[key], key


async def test_rnd_tests_samples_and_does_not_order(engine: AsyncEngine) -> None:
    """`sc_rnd` holds the R&D duty and only it of the step duties: it may
    not propose or request a sample (ordering), and nobody else tests one."""
    catalogue = await _catalogue(engine)
    rnd = handlers.duty_scope(CaseDuty.RND)
    assert rnd in catalogue["sc_rnd"]
    assert handlers.duty_scope(CaseDuty.ORDERING) not in catalogue["sc_rnd"]
    assert {key for key, scopes in catalogue.items() if rnd in scopes} == {"sc_rnd"}
    assert catalogue["sc_viewer"] <= catalogue["sc_rnd"]


async def test_rnd_holds_exactly_the_viewer_its_duty_and_the_document_write(
    engine: AsyncEngine,
) -> None:
    """Lead decision 8, whole: viewer (which carries the document and
    product-case reads), `duty.rnd`, the document write, and nothing else. In
    particular not `duty.exceptions`, which would let R&D pause, block and
    resume PO cases: that duty is not split by case kind."""
    catalogue = await _catalogue(engine)
    assert {handlers.DOCUMENT_READ, handlers.PRODUCT_CASE_READ} <= catalogue["sc_viewer"]
    assert catalogue["sc_rnd"] == catalogue["sc_viewer"] | {
        handlers.duty_scope(CaseDuty.RND),
        handlers.DOCUMENT_WRITE,
    }


async def test_the_supply_lead_holds_exactly_the_viewer_its_duty_and_the_document_write(
    engine: AsyncEngine,
) -> None:
    """Step 8 (ticket 03): TP Cung ứng confirms the product with the supplier
    and uploads the supplier's email, built like `sc_rnd`; nobody else holds
    the duty, and no `duty.exceptions` (shared with PO cases)."""
    catalogue = await _catalogue(engine)
    supply_lead = handlers.duty_scope(CaseDuty.SUPPLY_LEAD)
    assert catalogue["sc_supply_lead"] == catalogue["sc_viewer"] | {
        supply_lead,
        handlers.DOCUMENT_WRITE,
    }
    assert {key for key, scopes in catalogue.items() if supply_lead in scopes} == {"sc_supply_lead"}


async def test_bgd_holds_exactly_the_viewer_and_the_bgd_review_scope(
    engine: AsyncEngine,
) -> None:
    """Step 6 (ticket 02, lead decision 11): `sc_bod` reads what a viewer
    reads, product cases included, and decides BGĐ's reviews; it takes no
    step and adds no paper. Deciding also needs `approvals.decide`, which
    stays on the platform ladder."""
    catalogue = await _catalogue(engine)
    assert handlers.PRODUCT_CASE_READ in catalogue["sc_viewer"]
    assert catalogue["sc_bod"] == catalogue["sc_viewer"] | {_APPROVE_BOD}
    assert {key for key, scopes in catalogue.items() if _APPROVE_BOD in scopes} == {"sc_bod"}
    assert "approvals.decide" not in catalogue["sc_bod"]


async def test_kế_toán_signs_through_sc_finance_and_nobody_else(engine: AsyncEngine) -> None:
    """Step 9 (ticket 04, QE-16 open): the accounting sign-off's stamp is
    granted to the existing Kế toán role, not a new `sc_accounting`, and to
    no other role; BGĐ signs with the scope it reviews samples with."""
    catalogue = await _catalogue(engine)
    assert {_APPROVE_BOD, _APPROVE_ACCOUNTING} == _SIGNOFF_SCOPES
    holders = {key for key, scopes in catalogue.items() if _APPROVE_ACCOUNTING in scopes}
    assert holders == {"sc_finance"}
    assert "sc_accounting" not in catalogue
    assert handlers.duty_scope(CaseDuty.FINANCE) in catalogue["sc_finance"]
    assert await _violation(engine, "sc_finance", "approver") is None


async def test_a_bgd_member_gets_the_decide_right_from_the_platform_ladder(
    engine: AsyncEngine,
) -> None:
    """How an Elmich BGĐ member holds both scopes `decide` asks for:
    `sc_bod` beside a platform approval role or the `approver_boost` set."""
    catalogue = await _catalogue(engine)
    for platform_role in ("approver", "manager", "director"):
        assert "approvals.decide" in catalogue[platform_role], platform_role
    async with engine.connect() as conn:
        boost = await conn.scalar(
            text("SELECT scopes FROM platform.permission_sets WHERE key = 'approver_boost'")
        )
    assert "approvals.decide" in boost
    assert await _violation(engine, "sc_bod", "approver") is None


async def test_whoever_opens_a_po_case_opens_a_product_case_and_nobody_else(
    engine: AsyncEngine,
) -> None:
    """Lead decision 9: opening a product case has its own write, held by the
    roles that hold the PO write (today `sc_operator`)."""
    catalogue = await _catalogue(engine)
    openers = {key for key, scopes in catalogue.items() if handlers.PRODUCT_CASE_WRITE in scopes}
    assert openers == {key for key, scopes in catalogue.items() if handlers.PO_CASE_WRITE in scopes}
    assert openers == {"sc_operator"}


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
        # Whoever sets the process rules does not also decide BGĐ's reviews.
        (("sc_process_admin", "sc_bod"), "sod_sc_rules_vs_operations"),
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
        # No ordering-vs-R&D rule until Elmich answers QE-16.
        ("sc_rnd", "sc_operator"),
        ("sc_rnd", "sc_qc"),
        ("sc_bod", "sc_viewer"),
        # No R&D-vs-TP Cung ứng or ordering-vs-TP Cung ứng rule (QE-16).
        ("sc_supply_lead", "sc_rnd"),
        ("sc_supply_lead", "sc_operator"),
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
