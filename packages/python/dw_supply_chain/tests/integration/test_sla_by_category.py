"""Integration: SLA by Category and follow-ups for product cases and their PIC
(stage-1 ticket 06, ADR 0019), on the real database.

What only Postgres can show:

- a product case past its `bm04` days opens an `sla_breach` follow-up stamped
  with its PIC (a member of the case's workspace), and the PIC and the scope
  holders find it in their inbox, linked to the product case;
- a PIC who is no longer a member of the workspace is neither stamped nor
  told; the scope holders still are;
- a follow-up of a product case in workspace W1 is neither read nor closed
  from W2 of the same tenant (`follow_ups` RLS by workspace, d56da3dd2146),
  and the table refuses a follow-up naming both kinds of case, neither, or a
  product case of another workspace;
- tenant B's SLA override changes nothing of tenant A's, and B's sweep reads
  nothing of A's; the cross-tenant read lists product-case workspaces, ids only;
- an SLA override stored at 1.2.0 is upgraded to 2.0 by the migration's data
  step with every number kept, and reads back through the schema;
- reassigning the PIC keeps the PIC an open follow-up was stamped with, and
  reassigning a PO case's PIC leaves the product case's PIC as it was.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from supply_chain_harness import REPO_ROOT
from test_follow_ups import ALPHA, ALPHA_WS, BETA, BETA_WS, _member, _Stack, stack
from test_product_cases import _Db, _profiling

from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.adapters.persistence.scope_holders import SqlScopeHolders
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.notifications import NotificationService
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.adapters.persistence.follow_up_repository import (
    SqlFollowUpRepository,
    SqlWorkspacesWithCases,
)
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.product_case_repository import (
    SqlProductCaseRepository,
)
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.handlers import (
    CloseFollowUp,
    GetSLAPolicy,
    ListFollowUps,
    ReassignPOCasePic,
    duty_scope,
)
from dw_supply_chain.application.product_cases import ReassignProductCasePic
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy, load_supply_chain_sla_policy

pytestmark = pytest.mark.integration

__all__ = ["stack"]  # the fixture, imported for pytest

_SLA = load_supply_chain_sla_policy(REPO_ROOT / "configs" / "policies" / SLA_POLICY_FILE)
_MIGRATION = (
    REPO_ROOT
    / "db"
    / "migrations"
    / "versions"
    / "d56da3dd2146_supply_chain_sla_by_category_and_follow_.py"
)
_LEAD = duty_scope(CaseDuty.SUPPLY_LEAD)


def _db(stack: _Stack) -> _Db:
    return _Db(stack.sessions, stack.migrator)


async def _late_profile(
    stack: _Stack, pic: AccessContext, *, days: int = 3
) -> ProductDevelopmentCase:
    """A case of `pic`'s workspace whose PIC is `pic`, in `profile_in_progress`
    (BM04, 2 days in the shipped default) since `days` days. Its Category is
    the free text of a case opened before the list existed: evaluated on
    `default`."""
    case = await _profiling(_db(stack), pic)
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE supply_chain.product_dev_case_state_transitions"
                " SET occurred_at = now() - make_interval(days => :d)"
                " WHERE product_dev_case_id = :id"
            ),
            {"id": case.id.value, "d": days},
        )
    return case


async def _rows(stack: _Stack, case: ProductDevelopmentCase) -> list[sa.Row[tuple[object, ...]]]:
    async with stack.migrator.connect() as conn:
        return list(
            (
                await conn.execute(
                    sa.text(
                        "SELECT id, kind, milestone, status, recipient_user_id, workspace_id,"
                        " recipient_scopes FROM supply_chain.follow_ups"
                        " WHERE product_dev_case_id = :id ORDER BY opened_at"
                    ),
                    {"id": case.id.value},
                )
            ).all()
        )


async def _titles(stack: _Stack, person: AccessContext, code: str) -> list[tuple[str, str | None]]:
    inbox = NotificationService(SqlNotificationRepository(stack.sessions))
    return [(n.title, n.link) for n in (await inbox.latest(person)).items if code in n.title]


async def _leave(stack: _Stack, person: AccessContext) -> None:
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text("DELETE FROM platform.memberships WHERE user_id = :u AND workspace_id = :w"),
            {"u": person.principal_id, "w": person.workspace_id},
        )


async def _another_workspace(stack: _Stack) -> uuid.UUID:
    workspace = uuid.uuid4()
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                " VALUES (:id, :tenant, :slug, 'Phòng khác')"
            ),
            {"id": workspace, "tenant": ALPHA, "slug": f"s6-{workspace.hex[:8]}"},
        )
    return workspace


# ---- a product case past its days ---------------------------------------------


async def test_a_product_case_past_bm04_reaches_its_pic_and_the_scope_holders(
    stack: _Stack,
) -> None:
    pic = await _member(stack, "sc_operator")
    owner = await _member(stack, "sc_process_admin")
    case = await _late_profile(stack, pic)

    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))
    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))

    (row,) = await _rows(stack, case)
    assert (row.kind, row.milestone, row.status) == ("sla_breach", "bm04", "open")
    assert (row.recipient_user_id, row.workspace_id) == (pic.principal_id, ALPHA_WS)
    expected = [
        (
            f"Trễ SLA BM04: hồ sơ phát triển {case.proposal_code}",
            f"/supply-chain/product-cases/{case.id.value}",
        )
    ]
    assert await _titles(stack, pic, case.proposal_code) == expected
    assert await _titles(stack, owner, case.proposal_code) == expected


async def test_within_its_days_a_product_case_opens_nothing(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    case = await _late_profile(stack, pic, days=1)

    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))

    assert await _rows(stack, case) == []


async def test_a_pic_who_left_the_workspace_is_not_stamped_and_the_scopes_still_are(
    stack: _Stack,
) -> None:
    pic = await _member(stack, "sc_operator")
    owner = await _member(stack, "sc_process_admin")
    case = await _late_profile(stack, pic)
    await _leave(stack, pic)

    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))

    (row,) = await _rows(stack, case)
    assert row.recipient_user_id is None
    assert await _titles(stack, owner, case.proposal_code) != []
    assert await _titles(stack, pic, case.proposal_code) == []


# ---- workspace and tenant -------------------------------------------------------


async def test_a_follow_up_of_w1_is_neither_read_nor_closed_from_w2(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    case = await _late_profile(stack, pic)
    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))
    (row,) = await _rows(stack, case)
    w2 = await _another_workspace(stack)
    other = await _member(stack, "sc_process_admin", ALPHA, w2)
    repo = SqlFollowUpRepository(stack.sessions)

    listed = await ListFollowUps(repo, ScopeAuthorizationService()).handle(other)
    assert [v for v in listed if v.record.id == row.id] == []
    with pytest.raises(NotFoundError):
        await CloseFollowUp(
            repo, ScopeAuthorizationService(), Uuid4Generator(), SystemClock()
        ).handle(other, row.id, "không phải của tôi")
    # Under W2's settings the row is not there for a raw statement either.
    async with stack.sessions() as session, session.begin():
        for key, value in (("app.tenant_id", ALPHA), ("app.workspace_id", w2)):
            await session.execute(
                sa.text("SELECT set_config(:k, :v, true)"), {"k": key, "v": str(value)}
            )
        seen = await session.scalar(
            sa.text("SELECT count(*) FROM supply_chain.follow_ups WHERE id = :id"), {"id": row.id}
        )
        touched = await session.execute(
            sa.text(
                "UPDATE supply_chain.follow_ups SET status = 'done', closed_at = now()"
                " WHERE id = :id"
            ),
            {"id": row.id},
        )
    assert (seen, getattr(touched, "rowcount", None)) == (0, 0)
    (still,) = await _rows(stack, case)
    assert still.status == "open"
    # In its own workspace the PIC it was stamped for closes it.
    await CloseFollowUp(repo, ScopeAuthorizationService(), Uuid4Generator(), SystemClock()).handle(
        pic, row.id, None
    )
    (closed,) = await _rows(stack, case)
    assert closed.status == "done"


@pytest.mark.parametrize("which", ["both", "neither", "other_workspace"])
async def test_the_table_refuses_a_follow_up_naming_both_neither_or_another_workspaces_case(
    stack: _Stack, which: str
) -> None:
    pic = await _member(stack, "sc_operator")
    case = await _late_profile(stack, pic)
    po = POCase(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(ALPHA),
        workspace_id=WorkspaceId(ALPHA_WS),
        po_reference=f"PO-S6-{uuid.uuid4().hex[:6]}",
        supplier_name="K",
    )
    await SqlPOCaseRepository(stack.sessions).add(pic, po)
    workspace = ALPHA_WS if which != "other_workspace" else await _another_workspace(stack)
    columns = {
        "both": (po.id.value, case.id.value),
        "neither": (None, None),
        "other_workspace": (None, case.id.value),
    }[which]
    constraint = {
        "both": "ck_follow_ups_one_case",
        "neither": "ck_follow_ups_one_case",
        "other_workspace": "fk_follow_ups_tenant_id_product_dev_cases",
    }[which]

    with pytest.raises(IntegrityError, match=constraint):
        async with stack.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO supply_chain.follow_ups (id, tenant_id, workspace_id, po_case_id,"
                    " product_dev_case_id, kind, episode, days, recipient_scopes)"
                    " VALUES (:id, :t, :w, :po, :product, 'sla_breach', 'e', 1,"
                    " '[\"supply_chain.sla_policy.write\"]'::jsonb)"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": ALPHA,
                    "w": workspace,
                    "po": columns[0],
                    "product": columns[1],
                },
            )


async def test_tenant_b_neither_reads_as_cases_nor_changes_as_numbers(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    case = await _late_profile(stack, pic, days=1)  # under A's 2 days
    beta = await _member(stack, "sc_process_admin", BETA, BETA_WS)
    strict = _SLA.model_dump(mode="json")
    strict["default"]["bm04"] = {"duration": "0d", "status": "confirmed"}
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:i, :t, 'supply_chain_sla', CAST(:c AS jsonb))"
                " ON CONFLICT (tenant_id, policy_id) DO UPDATE SET content = EXCLUDED.content"
            ),
            {"i": uuid.uuid4(), "t": BETA, "c": json.dumps(strict)},
        )
    try:
        await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))
        await stack.sweep().sweep_workspace(sweep_context(BETA, BETA_WS))
        assert await _rows(stack, case) == []
        # B's own policy reads B's number; A's is untouched.
        read = GetSLAPolicy(
            SqlPolicyOverrideRepository(stack.sessions), _SLA, ScopeAuthorizationService()
        )
        mine = beta.model_copy(update={"scopes": frozenset({"supply_chain.sla_policy.read"})})
        assert (await read.handle(mine)).default["bm04"].duration == "0d"
    finally:
        async with stack.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.policy_overrides"
                    " WHERE tenant_id = :t AND policy_id = 'supply_chain_sla'"
                ),
                {"t": BETA},
            )


async def test_the_cross_tenant_read_lists_product_case_workspaces_ids_only(
    stack: _Stack,
) -> None:
    w2 = await _another_workspace(stack)
    pic = await _member(stack, "sc_operator", ALPHA, w2)
    await _late_profile(stack, pic)

    pairs = await SqlWorkspacesWithCases(stack.sessions).workspaces()

    assert (ALPHA, w2) in pairs
    assert all(len(pair) == 2 for pair in pairs)


# ---- an override stored at 1.2.0 ----------------------------------------------------


async def test_a_1_2_0_override_becomes_2_0_with_every_number_kept(stack: _Stack) -> None:
    tenant = uuid.uuid4()
    stored = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_sla",
        "policy_version": "1.2.0",
        "sla": {
            "bm04": {"duration": "4d", "status": "pending_business_confirmation"},
            "deposit": {"duration": "10d", "status": "confirmed", "description": "của Elmich"},
        },
        "supplier_update": {"reminder_after": "5d", "escalation_after": "10d"},
    }
    async with stack.migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.tenants (id, slug, name, status)"
                " VALUES (:t, :s, 'S6', 'active')"
            ),
            {"t": tenant, "s": f"s6-{tenant.hex[:8]}"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.policy_overrides (id, tenant_id, policy_id, content)"
                " VALUES (:i, :t, 'supply_chain_sla', CAST(:c AS jsonb))"
            ),
            {"i": uuid.uuid4(), "t": tenant, "c": json.dumps(stored)},
        )
    spec = importlib.util.spec_from_file_location("migration_d56da3dd2146", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with stack.migrator.begin() as conn:
        await conn.execute(sa.text(migration.UPGRADE_SLA_OVERRIDES))
        content = await conn.scalar(
            sa.text(
                "SELECT content FROM platform.policy_overrides"
                " WHERE tenant_id = :t AND policy_id = 'supply_chain_sla'"
            ),
            {"t": tenant},
        )

    policy = SupplyChainSLAPolicy.model_validate(content)
    assert policy.policy_version == "1.2.0"
    assert {k: v.model_dump(mode="json") for k, v in policy.default.items()} == {
        "bm04": {"duration": "4d", "status": "pending_business_confirmation", "description": ""},
        "deposit": {"duration": "10d", "status": "confirmed", "description": "của Elmich"},
    }
    assert policy.supplier_update.cadence.reminder_after_days == 5
    assert [c.key for c in policy.categories] == ["noi", "chao"]
    assert policy.by_category == {}
    assert "sla" not in content


# ---- reassign_pic ------------------------------------------------------------------


async def test_a_reassigned_pic_leaves_the_stamp_on_an_open_follow_up(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    # A viewer holds no recipient scope: whatever reaches them reaches the PIC.
    successor = await _member(stack, "sc_viewer")
    case = await _late_profile(stack, pic)
    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))

    lead = pic.model_copy(update={"scopes": frozenset({_LEAD}), "principal_id": uuid.uuid4()})
    await ReassignProductCasePic(
        repo=SqlProductCaseRepository(stack.sessions),
        members=SqlScopeHolders(stack.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(lead, case_id=case.id, new_pic=successor.principal_id, reason="Nghỉ phép")
    await stack.sweep().sweep_workspace(sweep_context(ALPHA, ALPHA_WS))

    found = await SqlProductCaseRepository(stack.sessions).get(pic, case.id)
    assert found is not None and found.pic_user_id == successor.principal_id
    (row,) = await _rows(stack, case)
    assert row.recipient_user_id == pic.principal_id
    assert await _titles(stack, successor, case.proposal_code) == []
    async with stack.migrator.connect() as conn:
        audited = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.audit_events"
                " WHERE action = 'supply_chain.product_case.reassign_pic' AND resource_id = :id"
            ),
            {"id": str(case.id)},
        )
    assert audited == 1


async def test_a_new_pic_must_be_a_member_of_the_cases_workspace(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    case = await _late_profile(stack, pic)
    w2 = await _another_workspace(stack)
    elsewhere = await _member(stack, "sc_operator", ALPHA, w2)
    lead = pic.model_copy(update={"scopes": frozenset({_LEAD})})

    with pytest.raises(Exception, match="member"):
        await ReassignProductCasePic(
            repo=SqlProductCaseRepository(stack.sessions),
            members=SqlScopeHolders(stack.sessions),
            authz=ScopeAuthorizationService(),
            ids=Uuid4Generator(),
            clock=SystemClock(),
        ).handle(lead, case_id=case.id, new_pic=elsewhere.principal_id, reason="x")


async def test_reassigning_a_po_cases_pic_leaves_the_product_cases_alone(stack: _Stack) -> None:
    pic = await _member(stack, "sc_operator")
    successor = await _member(stack, "sc_operator")
    product = await _late_profile(stack, pic)
    po = POCase.requested(
        id=POCaseId(uuid.uuid4()),
        tenant_id=TenantId(ALPHA),
        workspace_id=WorkspaceId(ALPHA_WS),
        supplier_name="K",
        product_dev_case_id=product.id.value,
        pic_user_id=pic.principal_id,
        category=product.category,
        lines=(),
    )
    await SqlPOCaseRepository(stack.sessions).add(pic, po)
    lead = pic.model_copy(update={"scopes": frozenset({_LEAD})})

    changed = await ReassignPOCasePic(
        repo=SqlPOCaseRepository(stack.sessions),
        members=SqlScopeHolders(stack.sessions),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(lead, po_case_id=po.id, new_pic=successor.principal_id, reason="Chuyển")

    stored = await SqlPOCaseRepository(stack.sessions).get(pic, po.id)
    assert (
        stored is not None and stored.pic_user_id == changed.pic_user_id == successor.principal_id
    )
    found = await SqlProductCaseRepository(stack.sessions).get(pic, product.id)
    assert found is not None and found.pic_user_id == pic.principal_id
    # A second save at the version read is refused: optimistic, as every step.
    stale = await SqlPOCaseRepository(stack.sessions).get(pic, po.id)
    assert stale is not None
    stale.version -= 1
    stale.reassign_pic(new_pic=pic.principal_id, reason="lại")
    with pytest.raises(ConflictError):
        await SqlPOCaseRepository(stack.sessions).save(pic, stale)


def test_this_file_is_where_its_migration_is() -> None:
    assert Path(_MIGRATION).is_file()
