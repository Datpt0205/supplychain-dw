"""Integration: step 12's colour, packaging and pre-production sub-flow and step
13's gate, against real Postgres as `dw_app` (slice PK).

What only the real database can show:

- the sub-flow through the handlers, each step with its history row (the
  colour's "đã báo TP MKT" among them) and its audit event in one transaction;
- with the tenant's rule on, `start_production` is refused until R&D passes
  the test, on the report of THIS case; with the rule off, nothing changes;
- a report of another case, of another tenant or workspace, is refused, and the
  history's FK refuses one written around the handler;
- the duty is checked before anything is read; `ordering` cannot pass the test,
  `rnd` cannot approve the colour;
- another tenant and another workspace of the same tenant neither read nor
  write `packaging_designs` or its history (RLS), and the FK refuses a tenant B
  row naming tenant A's PO case;
- a case that left `pre_production` refuses a step the handler already read it
  in (the saving transaction re-checks);
- a tenant's PO step-to-duty override stored before slice PK loads after the
  migration's data step, its own choices kept.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from supply_chain_harness import REPO_ROOT, DatabaseUrls
from test_case_documents import _add, _Db

from dw_agent_runtime.adapters.docx_templates import DocxRenderer
from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.action_duties import CaseDuty, load_supply_chain_action_duties
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.adapters.persistence.document_draft_repository import (
    SqlDocumentDraftRepository,
)
from dw_supply_chain.adapters.persistence.draft_filings import SqlDraftFilings
from dw_supply_chain.adapters.persistence.packaging_design_repository import (
    SqlPackagingDesignRepository,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.pre_production_measurements import (
    SqlPreProductionMeasurements,
)
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.application.document_drafts import DraftFiler
from dw_supply_chain.application.handlers import (
    DOCUMENT_READ,
    DOCUMENT_WRITE,
    PO_CASE_READ,
    AdvancePOCase,
    duty_scope,
)
from dw_supply_chain.application.packaging_designs import (
    GetPackagingDesign,
    SetPackagingPolicyOverride,
    TakePackagingStep,
)
from dw_supply_chain.application.po_case_audit import po_case_audit
from dw_supply_chain.application.pre_production_test import PreProductionTestSources
from dw_supply_chain.application.production_gate import ProductionGateResolver
from dw_supply_chain.approval_matrix import load_supply_chain_approval_matrix
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.packaging_design import (
    MKT_LEAD_NOTE,
    PackagingAction,
    PackagingDesign,
    PreProductionTest,
)
from dw_supply_chain.domain.po_case import CaseAction, CaseState, POCase, POCaseId
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
from dw_supply_chain.testing.po_papers import open_paper_gate
from dw_supply_chain.testing.step_preparation import (
    ELMICH_CRITERIA,
    InMemoryStorage,
    PlatformTemplates,
)

pytestmark = pytest.mark.integration

_POLICIES = REPO_ROOT / "configs" / "policies"
_DUTIES = load_supply_chain_action_duties(_POLICIES / "supply_chain_action_duties@1.3.0.yaml")
_PACKAGING = load_supply_chain_packaging_policy(_POLICIES / "supply_chain_packaging@1.1.0.yaml")
_MATRIX = load_supply_chain_approval_matrix(_POLICIES / "supply_chain_approval_matrix@1.0.0.yaml")
_MIGRATION = (
    REPO_ROOT
    / "db"
    / "migrations"
    / "versions"
    / "85659fd91943_supply_chain_packaging_designs_step_12.py"
)

ORDERING = duty_scope(CaseDuty.ORDERING)
RND = duty_scope(CaseDuty.RND)
A = PackagingAction


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, expire_on_commit=False), migrator)
    await app.dispose()
    await migrator.dispose()


@dataclass
class _Notices:
    sent: list[tuple[list[uuid.UUID], str]] = field(default_factory=list)

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None:
        self.sent.append((list(recipients), title))


def _person(tenant: uuid.UUID, workspace: uuid.UUID, *scopes: str) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset({PO_CASE_READ, DOCUMENT_READ, DOCUMENT_WRITE, *scopes}),
        plan_id="professional",
    )


@dataclass(frozen=True)
class _Team:
    """Cung ứng and R&D of one workspace; `case` sits in `pre_production`."""

    ordering: AccessContext
    rnd: AccessContext
    case: POCase


async def _team(db: _Db, *, pic: uuid.UUID | None = None) -> _Team:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    ordering = _person(tenant, workspace, ORDERING, duty_scope(CaseDuty.FINANCE))
    case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        po_reference=f"PO-PK-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC Bao bì",
        pic_user_id=pic,
    )
    repo = SqlPOCaseRepository(db.sessions)
    await repo.add(ordering, case)
    for step in (case.request_deposit, case.confirm_deposit, case.start_pre_production):
        step()
        await repo.save(ordering, case)
    return _Team(ordering, _person(tenant, workspace, RND), case)


def _take(db: _Db, notices: _Notices | None = None) -> TakePackagingStep:
    return TakePackagingStep(
        po_cases=SqlPOCaseRepository(db.sessions),
        designs=SqlPackagingDesignRepository(db.sessions),
        documents=SqlCaseDocumentRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_duties=_DUTIES,
        platform_default_policy=_PACKAGING,
        holders=SqlScopeHolders(db.sessions),
        notifier=notices or _Notices(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
        drafts=SqlDocumentDraftRepository(db.sessions),
        test=PreProductionTestSources(
            measurements=SqlPreProductionMeasurements(db.sessions),
            designs=SqlPackagingDesignRepository(db.sessions),
            product_cases=SqlProductCaseRepository(db.sessions),
            policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
            platform_default_criteria=ELMICH_CRITERIA,
        ),
        filer=DraftFiler(
            templates=PlatformTemplates(),
            renderer=DocxRenderer(),
            storage=InMemoryStorage(),
            ids=Uuid4Generator(),
        ),
        filings=SqlDraftFilings(db.sessions),
    )


def _get(db: _Db) -> GetPackagingDesign:
    return GetPackagingDesign(
        po_cases=SqlPOCaseRepository(db.sessions),
        designs=SqlPackagingDesignRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_duties=_DUTIES,
        platform_default_policy=_PACKAGING,
        documents=SqlCaseDocumentRepository(db.sessions),
    )


def _advance(db: _Db) -> AdvancePOCase:
    return AdvancePOCase(
        repo=SqlPOCaseRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        platform_default_approval_matrix=_MATRIX,
        platform_default_action_duties=_DUTIES,
        runner=None,  # type: ignore[arg-type]  # the matrix gates nothing: no run starts
        production_gate=ProductionGateResolver(
            designs=SqlPackagingDesignRepository(db.sessions),
            policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
            platform_default=_PACKAGING,
        ),
        papers=open_paper_gate(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    )


async def _require_the_test(db: _Db, team: _Team) -> None:
    admin = _person(
        team.ordering.tenant_id, team.ordering.workspace_id, "supply_chain.action_duties.write"
    )
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :s, 'Tenant PK')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": admin.tenant_id, "s": f"pk-{admin.tenant_id.hex[:8]}"},
        )
    await SetPackagingPolicyOverride(
        policy_override_repo=SqlPolicyOverrideRepository(db.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(admin, _PACKAGING.model_copy(update={"require_pre_production_test": True}))


async def _to_sample(db: _Db, team: _Team) -> None:
    take = _take(db)
    for action in (A.APPROVE_COLOUR, A.APPROVE_DESIGN, A.RECEIVE_PRE_PRODUCTION_SAMPLE):
        await take.handle(team.ordering, po_case_id=team.case.id, action=action)


async def _report(db: _Db, team: _Team, case: POCase | None = None) -> uuid.UUID:
    document = await _add(
        db,
        team.rnd,
        case or team.case,
        DocumentType.PRE_PRODUCTION_TEST_REPORT,
    )
    return document.id.value


async def _audit_actions(db: _Db, case: POCase) -> list[str]:
    async with db.migrator.connect() as conn:
        return list(
            (
                await conn.execute(
                    sa.text(
                        "SELECT action FROM platform.audit_events"
                        " WHERE resource_type = 'po_case' AND resource_id = :c"
                        " ORDER BY occurred_at"
                    ),
                    {"c": str(case.id)},
                )
            ).scalars()
        )


# --- the sub-flow and the gate ---------------------------------------------------


async def test_the_sub_flow_records_each_step_and_the_rule_holds_production_until_the_test(
    db: _Db,
) -> None:
    pic = uuid.uuid4()
    team = await _team(db, pic=pic)
    await _require_the_test(db, team)
    notices = _Notices()
    take = _take(db, notices)
    advance = _advance(db)

    await take.handle(
        team.ordering, po_case_id=team.case.id, action=A.REQUEST_COLOUR_REVISION, reason="lệch màu"
    )
    await take.handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)
    assert notices.sent == [([pic], "Màu đã được duyệt, đã báo TP MKT")]
    await take.handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_DESIGN)
    await take.handle(
        team.ordering, po_case_id=team.case.id, action=A.RECEIVE_PRE_PRODUCTION_SAMPLE
    )

    with pytest.raises(ConflictError) as held:
        await advance.handle(
            team.ordering, po_case_id=team.case.id, action=CaseAction.START_PRODUCTION
        )
    assert held.value.details["reason"] == "pre_production_test_not_passed"

    await take.handle(
        team.rnd,
        po_case_id=team.case.id,
        action=A.FAIL_PRE_PRODUCTION_TEST,
        reason="bong lớp phủ",
        document_id=await _report(db, team),
    )
    with pytest.raises(ConflictError):
        await advance.handle(
            team.ordering, po_case_id=team.case.id, action=CaseAction.START_PRODUCTION
        )

    await take.handle(
        team.rnd,
        po_case_id=team.case.id,
        action=A.PASS_PRE_PRODUCTION_TEST,
        document_id=await _report(db, team),
    )
    result = await advance.handle(
        team.ordering, po_case_id=team.case.id, action=CaseAction.START_PRODUCTION
    )
    assert result.case.state is CaseState.PRODUCTION  # type: ignore[union-attr]

    detail = await _get(db).handle(team.ordering, team.case.id)
    assert detail.require_pre_production_test is True
    assert detail.design.pre_production_test is PreProductionTest.PASSED
    assert [e.action for e in detail.history] == [
        A.REQUEST_COLOUR_REVISION,
        A.APPROVE_COLOUR,
        A.APPROVE_DESIGN,
        A.RECEIVE_PRE_PRODUCTION_SAMPLE,
        A.FAIL_PRE_PRODUCTION_TEST,
        A.PASS_PRE_PRODUCTION_TEST,
    ]
    assert [e.note for e in detail.history if e.note] == [MKT_LEAD_NOTE]
    assert {e.actor_id for e in detail.history[-2:]} == {team.rnd.principal_id}
    audited = await _audit_actions(db, team.case)
    for action in (*(e.action.value for e in detail.history), "start_production"):
        assert f"supply_chain.po_case.{action}" in audited, action

    # A step after the case moved on is refused.
    with pytest.raises(ConflictError):
        await take.handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)


async def test_with_the_rule_off_production_opens_as_before(db: _Db) -> None:
    team = await _team(db)
    result = await _advance(db).handle(
        team.ordering, po_case_id=team.case.id, action=CaseAction.START_PRODUCTION
    )
    assert result.case.state is CaseState.PRODUCTION  # type: ignore[union-attr]
    detail = await _get(db).handle(team.ordering, team.case.id)
    assert detail.require_pre_production_test is False


async def test_the_page_is_told_who_may_take_each_step_by_the_steps_own_check(db: _Db) -> None:
    team = await _team(db)
    by_ordering = {
        s.action: s.allowed for s in (await _get(db).handle(team.ordering, team.case.id)).steps
    }
    by_rnd = {s.action: s.allowed for s in (await _get(db).handle(team.rnd, team.case.id)).steps}
    tests = {A.PASS_PRE_PRODUCTION_TEST, A.FAIL_PRE_PRODUCTION_TEST}
    assert by_ordering == {a: a not in tests for a in A}
    assert by_rnd == {a: a in tests for a in A}


# --- the duty, before anything is read ----------------------------------------


async def test_ordering_cannot_pass_the_test_and_rnd_cannot_approve_the_colour(db: _Db) -> None:
    team = await _team(db)
    await _to_sample(db, team)
    report = await _report(db, team)
    with pytest.raises(PermissionDeniedError):
        await _take(db).handle(
            team.ordering,
            po_case_id=team.case.id,
            action=A.PASS_PRE_PRODUCTION_TEST,
            document_id=report,
        )
    other = await _team(db)
    with pytest.raises(PermissionDeniedError):
        await _take(db).handle(other.rnd, po_case_id=other.case.id, action=A.APPROVE_COLOUR)
    assert (await _get(db).handle(team.ordering, team.case.id)).design.pre_production_test is (
        PreProductionTest.PENDING
    )


# --- the paper -----------------------------------------------------------------


async def test_a_report_of_another_case_or_tenant_or_workspace_is_refused(db: _Db) -> None:
    team = await _team(db)
    await _to_sample(db, team)
    sibling = await _team(db)
    same_tenant_other_ws = _person(team.rnd.tenant_id, uuid.uuid4(), RND)
    other_ws_case = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(same_tenant_other_ws.tenant_id),
        workspace_id=WorkspaceId(same_tenant_other_ws.workspace_id),
        po_reference=f"PO-PK-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC",
    )
    await SqlPOCaseRepository(db.sessions).add(same_tenant_other_ws, other_ws_case)
    another_case_same_ws = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=team.case.tenant_id,
        workspace_id=team.case.workspace_id,
        po_reference=f"PO-PK-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC",
    )
    await SqlPOCaseRepository(db.sessions).add(team.ordering, another_case_same_ws)
    foreign = [
        await _report(db, team, another_case_same_ws),
        await _report(db, sibling),
        (
            await _add(
                db,
                same_tenant_other_ws,
                other_ws_case,
                DocumentType.PRE_PRODUCTION_TEST_REPORT,
            )
        ).id.value,
        uuid.uuid4(),
    ]
    for document_id in foreign:
        with pytest.raises(ConflictError) as refused:
            await _take(db).handle(
                team.rnd,
                po_case_id=team.case.id,
                action=A.PASS_PRE_PRODUCTION_TEST,
                document_id=document_id,
            )
        assert refused.value.details["missing_document_type"] == "pre_production_test_report"
    with pytest.raises(ConflictError):
        await _take(db).handle(team.rnd, po_case_id=team.case.id, action=A.PASS_PRE_PRODUCTION_TEST)
    assert (await _get(db).handle(team.rnd, team.case.id)).design.pre_production_test is (
        PreProductionTest.PENDING
    )


async def test_the_history_fk_refuses_another_cases_report_written_around_the_handler(
    db: _Db,
) -> None:
    team = await _team(db)
    await _to_sample(db, team)
    other = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=team.case.tenant_id,
        workspace_id=team.case.workspace_id,
        po_reference=f"PO-PK-{uuid.uuid4().hex[:8]}",
        supplier_name="NCC",
    )
    await SqlPOCaseRepository(db.sessions).add(team.ordering, other)
    foreign = await _report(db, team, other)
    with pytest.raises(IntegrityError, match="fk_packaging_design_events_tenant_id_case_documents"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(team.rnd)) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO supply_chain.packaging_design_events"
                    " (id, tenant_id, workspace_id, po_case_id, action, actor_id, document_id)"
                    " VALUES (:i, :t, :w, :c, 'pass_pre_production_test', :a, :d)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": team.rnd.tenant_id,
                    "w": team.rnd.workspace_id,
                    "c": team.case.id.value,
                    "a": team.rnd.principal_id,
                    "d": foreign,
                },
            )


# --- tenancy ---------------------------------------------------------------------


async def test_another_tenant_or_workspace_neither_reads_nor_writes_the_sub_flow(db: _Db) -> None:
    team = await _team(db)
    await _take(db).handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)
    stranger = _person(uuid.uuid4(), uuid.uuid4(), ORDERING)
    neighbour = _person(team.ordering.tenant_id, uuid.uuid4(), ORDERING)
    for outsider in (stranger, neighbour):
        with pytest.raises(NotFoundError):
            await _get(db).handle(outsider, team.case.id)
        with pytest.raises(NotFoundError):
            await _take(db).handle(outsider, po_case_id=team.case.id, action=A.APPROVE_DESIGN)
        async with tenant_session(db.sessions, TenantScope.from_access_context(outsider)) as s:
            for table in ("packaging_designs", "packaging_design_events"):
                seen = await s.scalar(
                    sa.text(f"SELECT count(*) FROM supply_chain.{table} WHERE po_case_id = :c"),
                    {"c": team.case.id.value},
                )
                assert seen == 0, table
            moved = await s.execute(
                sa.text(
                    "UPDATE supply_chain.packaging_designs SET design_status = 'approved'"
                    " WHERE po_case_id = :c"
                ),
                {"c": team.case.id.value},
            )
            assert moved.rowcount == 0  # type: ignore[attr-defined]
    detail = await _get(db).handle(team.ordering, team.case.id)
    assert detail.design.design_status.value == "pending"


async def test_a_tenant_b_row_naming_tenant_as_po_case_is_refused(db: _Db) -> None:
    team = await _team(db)
    stranger = _person(uuid.uuid4(), uuid.uuid4(), ORDERING)
    with pytest.raises(IntegrityError, match="fk_packaging_designs_tenant_id_po_cases"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(stranger)) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO supply_chain.packaging_designs"
                    " (id, tenant_id, workspace_id, po_case_id, version)"
                    " VALUES (:i, :t, :w, :c, 1)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": stranger.tenant_id,
                    "w": stranger.workspace_id,
                    "c": team.case.id.value,
                },
            )
    # And RLS refuses a row stamped with tenant A from tenant B's session.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(stranger)) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO supply_chain.packaging_designs"
                    " (id, tenant_id, workspace_id, po_case_id, version)"
                    " VALUES (:i, :t, :w, :c, 1)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": team.ordering.tenant_id,
                    "w": team.ordering.workspace_id,
                    "c": team.case.id.value,
                },
            )


async def test_the_table_keeps_the_sub_flows_order_whatever_writes_it(db: _Db) -> None:
    team = await _team(db)
    with pytest.raises(IntegrityError, match="ck_packaging_designs_order"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(team.rnd)) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO supply_chain.packaging_designs"
                    " (id, tenant_id, workspace_id, po_case_id, pre_production_test, version)"
                    " VALUES (:i, :t, :w, :c, 'passed', 1)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": team.rnd.tenant_id,
                    "w": team.rnd.workspace_id,
                    "c": team.case.id.value,
                },
            )


# --- the override migration -----------------------------------------------------


async def test_an_override_stored_before_slice_pk_loads_after_the_migration(db: _Db) -> None:
    team = await _team(db)
    stored = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_action_duties",
        "policy_version": "1.1.0",
        "action_duties": {
            a.value: d.value for a, d in _DUTIES.action_duties.items() if a not in set(A)
        }
        | {"request_deposit": "exceptions"},
    }
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :s, 'Tenant PK')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": team.ordering.tenant_id, "s": f"pk-{team.ordering.tenant_id.hex[:8]}"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:i, :t, 'supply_chain_action_duties', CAST(:c AS jsonb))"
            ),
            {"i": uuid.uuid4(), "t": team.ordering.tenant_id, "c": json.dumps(stored)},
        )
    with pytest.raises(ValidationError, match="approve_colour"):
        await _take(db).handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)

    spec = importlib.util.spec_from_file_location("migration_85659fd91943", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with db.migrator.begin() as conn:
        await conn.execute(sa.text(migration.ADD_PACKAGING_STEPS_TO_OVERRIDES))

    await _take(db).handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)
    async with db.migrator.connect() as conn:
        content = await conn.scalar(
            sa.text(
                "SELECT content FROM platform.policy_overrides"
                " WHERE tenant_id = :t AND policy_id = 'supply_chain_action_duties'"
            ),
            {"t": team.ordering.tenant_id},
        )
    assert content["action_duties"]["request_deposit"] == "exceptions"
    assert content["action_duties"]["pass_pre_production_test"] == "rnd"
    assert content["action_duties"]["approve_colour"] == "ordering"


async def test_a_case_that_left_pre_production_since_it_was_read_refuses_the_save(
    db: _Db,
) -> None:
    """The handler read the case in `pre_production`; meanwhile it went into
    production. The saving transaction looks again and refuses."""
    team = await _team(db)
    design = PackagingDesign(
        po_case_id=team.case.id.value,
        tenant_id=team.case.tenant_id,
        workspace_id=team.case.workspace_id,
    )
    design.take(
        A.APPROVE_COLOUR,
        case_in_pre_production=True,
        reason=None,
        document=None,
        now=SystemClock().now(),
        mkt_required=False,
    )
    await _advance(db).handle(
        team.ordering, po_case_id=team.case.id, action=CaseAction.START_PRODUCTION
    )
    with pytest.raises(ConflictError, match="bước 12"):
        await SqlPackagingDesignRepository(db.sessions).save(
            team.ordering,
            design,
            actor_id=team.ordering.principal_id,
            audit=po_case_audit(
                team.ordering, Uuid4Generator(), SystemClock(), team.case.id, "approve_colour", {}
            ),
        )
    assert (
        await SqlPackagingDesignRepository(db.sessions).get(team.ordering, team.case.id.value)
        is None
    )


# --- MKT at step 12 (ticket ai-automation/16) ---------------------------------


async def test_mkts_steps_are_stored_in_order_and_the_table_refuses_content_before_the_pack(
    db: _Db,
) -> None:
    """Migration 4527f22c2031: the two new columns, the event CHECKs (the
    content step carries its paper) and the row's order, whatever writes it."""
    team = await _team(db)
    mkt = _person(team.ordering.tenant_id, team.ordering.workspace_id, duty_scope(CaseDuty.MKT))
    async with db.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name) VALUES (:t, :s, 'Tenant MKT')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": team.ordering.tenant_id, "s": f"mkt-{team.ordering.tenant_id.hex[:8]}"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:i, :t, 'supply_chain_packaging', CAST(:c AS jsonb))"
            ),
            {
                "i": uuid.uuid4(),
                "t": team.ordering.tenant_id,
                "c": json.dumps(
                    _PACKAGING.model_dump(mode="json") | {"require_packaging_content": True}
                ),
            },
        )
    await _take(db).handle(team.ordering, po_case_id=team.case.id, action=A.APPROVE_COLOUR)
    await _take(db).handle(team.ordering, po_case_id=team.case.id, action=A.SEND_MKT_PACK)
    content = await _add(db, mkt, team.case, DocumentType.PACKAGING_CONTENT)
    await _take(db).handle(
        mkt,
        po_case_id=team.case.id,
        action=A.SUBMIT_PACKAGING_CONTENT,
        document_id=content.id.value,
    )
    design = await SqlPackagingDesignRepository(db.sessions).get(team.ordering, team.case.id.value)
    assert design is not None
    assert design.mkt_pack_sent_at is not None and design.packaging_content_submitted_at is not None
    with pytest.raises(IntegrityError, match="ck_packaging_designs_order"):
        async with tenant_session(db.sessions, TenantScope.from_access_context(mkt)) as s:
            await s.execute(
                sa.text(
                    "UPDATE supply_chain.packaging_designs SET mkt_pack_sent_at = NULL"
                    " WHERE po_case_id = :c"
                ),
                {"c": team.case.id.value},
            )
