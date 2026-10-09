"""Seed the local dev database with Supply Chain demo data, for trying it in a browser.

Usage (reads `.env`; `make` exports it, a plain shell needs `set -a; source .env`):
    uv run python scripts/seed_supply_chain_demo.py seed
    uv run python scripts/seed_supply_chain_demo.py supplier-update PO-DEMO-001
    uv run python scripts/seed_supply_chain_demo.py elmich-sla
    uv run python scripts/seed_supply_chain_demo.py elmich-packaging
    uv run python scripts/seed_supply_chain_demo.py elmich-step-preparation
    uv run python scripts/seed_supply_chain_demo.py e2e-fixtures
    uv run python scripts/seed_supply_chain_demo.py e2e-cleanup [E2E-<code>]

`seed` is idempotent:
- the platform demo roster (tenants, workspaces, users, memberships, plans),
  via the same `seed_test_env` the tests use;
- Supply Chain personas for the demo users of tenant Alpha, replacing the
  roles the platform roster gives them (a sales product's, carried over):
    - An: `sc_operator`, the coordinator who records supplier updates;
    - Bình: `sc_process_admin`, the process owner who sets the SLA;
    - Diệu: `sc_finance`, who confirms deposits and payments;
    - Giang: `sc_qc`, who passes or fails QC;
    - Hà: `sc_logistics` and `sc_warehouse`, port arrival and receiving;
    - Chi keeps `platform_admin`, the administrator;
- three personas the platform roster has no user for, created in Alpha's main
  workspace, so stage 1 steps 1-8 can be clicked through:
    - Linh: `sc_rnd`, who receives, tests and passes samples (steps 2-5) and
      completes the BM04 (step 7);
    - Khánh: BGĐ, `sc_bod` beside the platform `approver` role, which is
      where `approvals.decide` comes from; decides step 6 at /approvals;
    - Tuấn: TP Cung ứng, `sc_supply_lead`, who confirms the product with the
      supplier on its email (step 8);
- four PO cases, backdated so each signal is already due:
    - PO-DEMO-001: opened 25 hours ago, no supplier update (a reminder);
    - PO-DEMO-002: opened 73 hours ago, no supplier update (an escalation);
    - PO-DEMO-003: waiting for its deposit for 49 hours against a 1-day SLA
      (an SLA breach), with a fresh supplier update;
    - PO-DEMO-004: opened now with a fresh supplier update (nothing due).

`supplier-update <PO>` records a supplier update on that case now, as An, so
the next sweep resolves its follow-up.

`elmich-sla` writes Elmich's own SLA numbers and Category list
(`scripts/elmich_sla_override.yaml`, provisional: every number pending
QE-04/QE-05, the list pending QE-13) as tenant Alpha's override, the tenant
that stands for Elmich on its own database (QO-1), as Bình. It goes through
`SetSLAPolicyOverride`, the handler behind `PUT /sla-policy`: the same schema,
the same `supply_chain.sla_policy.write` check, the same audit event; not a
migration, since one customer's numbers are that tenant's data. Not part of
`seed`: pending numbers raise no SLA signal, and the demo's PO-DEMO-003 shows
one. Run it again after editing the file; a later `PUT` replaces it whole.

`elmich-packaging` turns on Elmich's step-13 rule
(`scripts/elmich_packaging_override.yaml`: a PO case enters production only
after R&D passes the pre-production test; slice PK) for tenant Alpha, as Bình,
through `SetPackagingPolicyOverride`, the handler behind `PUT
/packaging-policy`, for the same reasons as `elmich-sla`.

`elmich-step-preparation` turns on the steps AI prepares for tenant Alpha
(`scripts/elmich_step_preparation_override.yaml`; ticket ai-automation/05),
as Bình, through `SetStepPreparationPolicyOverride`, the handler behind
`PUT /step-preparation-policy`, for the same reasons.

`e2e-fixtures` is what the browser suite (`apps/web/e2e/supply-chain.spec.ts`)
needs beyond `seed`: Khánh linked to a Zalo chat that does not exist
(`e2e-chat-khanh`), so /approvals/<id> offers a code once a comment is
written, and a second Alpha workspace ("Kho Hưng Yên") he is also BGĐ of, so
/settings shows the "Workspace dùng cho Zalo" select. A worker polling a real
bot would fail to deliver to that chat and say so; nothing else reads it.

`e2e-cleanup` removes what that suite leaves behind, so the dev database does
not collect one product case per run: Alpha's product cases proposed as
`E2E-…` (or exactly the code given), with the approvals raised for them (their
decisions and view receipts cascade) and the notices linking to them. The case's
children go by its cascade; an uploaded paper's object is left to the orphan
sweep (`supply_chain_document_orphans`). Audit rows stay: the audit log is
append-only, and a record of what a test did is still a record. A case that
reached ĐẶT HÀNG is kept (its PO case RESTRICTs it) and named. The browser suite
runs it after its walk with the walk's own code.

Refuses to run unless DW_API_PROFILE is explicitly `local` (unset is refused):
it writes through the migrator role and backdates rows, which no deployed
database should ever see.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa
import yaml
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.policy_overrides import SqlPolicyOverrideRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_WRITE,
    SLA_POLICY_WRITE,
    SetSLAPolicyOverride,
)
from dw_supply_chain.application.packaging_designs import SetPackagingPolicyOverride
from dw_supply_chain.application.step_proposals import SetStepPreparationPolicyOverride
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)
from dw_supply_chain.packaging_policy import load_supply_chain_packaging_policy
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy
from dw_supply_chain.step_preparation_policy import load_supply_chain_step_preparation

ELMICH_SLA = Path(__file__).resolve().parent / "elmich_sla_override.yaml"
ELMICH_PACKAGING = Path(__file__).resolve().parent / "elmich_packaging_override.yaml"
ELMICH_STEP_PREPARATION = Path(__file__).resolve().parent / "elmich_step_preparation_override.yaml"
# Bình, `sc_process_admin`: the persona who sets the SLA.
PROCESS_OWNER = "dev|binh.tran"

ALPHA = uuid.UUID("d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d")
ALPHA_WS = uuid.UUID("64764894-718d-5558-ba17-9a2949214063")
# Exactly these roles, so a persona's screen shows its Supply Chain duties
# and nothing left over from the roster's sales roles.
ROLES = {
    "dev|an.nguyen": ["member", "sc_operator"],
    "dev|binh.tran": ["member", "sc_process_admin"],
    "dev|dieu.hoang": ["member", "sc_finance"],
    "dev|giang.do": ["member", "sc_qc"],
    "dev|ha.vu": ["member", "sc_logistics", "sc_warehouse"],
}
COORDINATOR = "dev|an.nguyen"
# Personas created here rather than by the platform roster: Supply Chain's own
# stage-1 roles. (subject, email, display name, department, roles). The BGĐ
# member decides with `approvals.decide` from the platform `approver` role and
# `supply_chain.approve.bod` from `sc_bod`: `decide` asks for both.
PERSONAS = (
    ("dev|linh.phan", "linh.phan@alpha.local", "Phan Thùy Linh", "rnd", ["member", "sc_rnd"]),
    ("dev|khanh.ngo", "khanh.ngo@alpha.local", "Ngô Minh Khánh", "bgd", ["approver", "sc_bod"]),
    (
        "dev|tuan.le",
        "tuan.le@alpha.local",
        "Lê Anh Tuấn",
        "supply",
        ["member", "sc_supply_lead"],
    ),
)
# po_reference, supplier, hours since opened, hours in WAITING_DEPOSIT, fresh update
CASES = (
    ("PO-DEMO-001", "Kangaroo", 25, None, False),
    ("PO-DEMO-002", "Sunhouse", 73, None, False),
    ("PO-DEMO-003", "Toshiba Việt Nam", 50, 49, True),
    ("PO-DEMO-004", "Elmich", 0, None, True),
)


def _urls() -> tuple[str, str]:
    # Fail closed: an unset profile is refused too, so a shell that never
    # sourced .env but holds a deployed DW_DATABASE_URL seeds nothing.
    if os.environ.get("DW_API_PROFILE") != "local":
        sys.exit(
            "refusing: this seeds demo data and backdates rows; "
            "set DW_API_PROFILE=local (local profile only)"
        )
    migrator, app = os.environ.get("DW_DATABASE_URL"), os.environ.get("DW_API_DATABASE_URL")
    if not migrator or not app:
        sys.exit("DW_DATABASE_URL and DW_API_DATABASE_URL must be set (source .env)")
    return migrator, app


async def _user_id(migrator: AsyncEngine, subject: str) -> uuid.UUID:
    async with migrator.connect() as conn:
        found = await conn.scalar(
            sa.text("SELECT id FROM platform.users WHERE subject = :s"), {"s": subject}
        )
    if found is None:
        sys.exit(f"{subject} is not seeded; run `seed` first")
    return uuid.UUID(str(found))


def _context(principal: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=ALPHA,
        workspace_id=ALPHA_WS,
        principal_id=principal,
        roles=frozenset({"sc_operator"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _update(case: POCase, text: str) -> SupplierUpdate:
    return SupplierUpdate(
        id=SupplierUpdateId(uuid.uuid4()),
        tenant_id=case.tenant_id,
        workspace_id=case.workspace_id,
        po_case_id=case.id,
        raw_text=text,
        extraction=SupplierUpdateExtraction(
            event_type=SupplierEventType.SHIPMENT_UPDATE,
            confidence=0.95,
            source_ref=text,
            reason="cập nhật định kỳ từ nhà cung cấp",
            proposed_action="không cần hành động",
        ),
        requires_confirmation=False,
    )


async def seed(migrator: AsyncEngine, app: AsyncEngine, migrator_url: str) -> None:
    await seed_test_env(migrator_url)
    async with migrator.begin() as conn:
        for subject, roles in ROLES.items():
            await conn.execute(
                sa.text(
                    "UPDATE platform.memberships m SET role_keys = CAST(:roles AS jsonb)"
                    " FROM platform.users u"
                    " WHERE u.id = m.user_id AND u.subject = :subject AND m.tenant_id = :tenant"
                ),
                {"subject": subject, "roles": json.dumps(roles), "tenant": ALPHA},
            )
    print("roles: " + ", ".join(f"{s.split('|')[1]} {'+'.join(r[1:])}" for s, r in ROLES.items()))
    await _personas(migrator)

    sessions = async_sessionmaker(app, expire_on_commit=False)
    context = _context(await _user_id(migrator, COORDINATOR))
    cases, updates = SqlPOCaseRepository(sessions), SqlSupplierUpdateRepository(sessions)
    for reference, supplier, opened_hours, waiting_hours, fresh_update in CASES:
        if await cases.find_by_reference(context, reference):
            print(f"{reference}: already there")
            continue
        case = POCase(
            id=POCaseId(uuid.uuid4()),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            po_reference=reference,
            supplier_name=supplier,
        )
        await cases.add(context, case)
        if waiting_hours is not None:
            case.request_deposit()
            await cases.save(context, case)
        async with migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE supply_chain.po_cases"
                    " SET created_at = now() - make_interval(hours => :h) WHERE id = :id"
                ),
                {"h": opened_hours, "id": case.id.value},
            )
            if waiting_hours is not None:
                await conn.execute(
                    sa.text(
                        "UPDATE supply_chain.po_case_state_transitions"
                        " SET occurred_at = now() - make_interval(hours => :h)"
                        " WHERE po_case_id = :id"
                    ),
                    {"h": waiting_hours, "id": case.id.value},
                )
        if fresh_update:
            await updates.add(context, _update(case, "Đang sản xuất đúng tiến độ."))
        print(f"{reference}: created ({supplier})")


async def _personas(migrator: AsyncEngine) -> None:
    """Create (or refresh) the stage-1 personas and their Alpha membership.
    The membership trigger still checks separation of duties."""
    async with migrator.begin() as conn:
        for subject, email, name, department, roles in PERSONAS:
            user_id = await conn.scalar(
                sa.text(
                    "INSERT INTO platform.users (id, subject, email, display_name)"
                    " VALUES (:id, :subject, :email, :name)"
                    " ON CONFLICT (subject) DO UPDATE"
                    " SET email = EXCLUDED.email, display_name = EXCLUDED.display_name"
                    " RETURNING id"
                ),
                {"id": uuid.uuid4(), "subject": subject, "email": email, "name": name},
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.memberships"
                    " (id, tenant_id, workspace_id, user_id, role_keys, department)"
                    " VALUES (:id, :tenant, :workspace, :user, CAST(:roles AS jsonb), :department)"
                    " ON CONFLICT ON CONSTRAINT uq_memberships_scope_user DO UPDATE"
                    " SET role_keys = EXCLUDED.role_keys, department = EXCLUDED.department"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": ALPHA,
                    "workspace": ALPHA_WS,
                    "user": user_id,
                    "roles": json.dumps(roles),
                    "department": department,
                },
            )
    print("personas: " + ", ".join(f"{s.split('|')[1]} {'+'.join(r)}" for s, *_, r in PERSONAS))


async def supplier_update(migrator: AsyncEngine, app: AsyncEngine, reference: str) -> None:
    sessions = async_sessionmaker(app, expire_on_commit=False)
    context = _context(await _user_id(migrator, COORDINATOR))
    found = await SqlPOCaseRepository(sessions).find_by_reference(context, reference)
    if not found:
        sys.exit(f"{reference}: no such case")
    await SqlSupplierUpdateRepository(sessions).add(
        context, _update(found[0], "Hàng đang sản xuất, dự kiến xuất đúng hẹn.")
    )
    print(f"{reference}: supplier update recorded; the next sweep resolves its follow-up")


async def elmich_sla(migrator: AsyncEngine, app: AsyncEngine) -> None:
    policy = SupplyChainSLAPolicy.model_validate(
        yaml.safe_load(ELMICH_SLA.read_text(encoding="utf-8"))
    )
    owner = _context(await _user_id(migrator, PROCESS_OWNER)).model_copy(
        update={"roles": frozenset({"sc_process_admin"}), "scopes": frozenset({SLA_POLICY_WRITE})}
    )
    await SetSLAPolicyOverride(
        policy_override_repo=SqlPolicyOverrideRepository(async_sessionmaker(app)),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(owner, policy)
    print(
        "elmich SLA override written for tenant Alpha: "
        + ", ".join(c.label for c in policy.categories)
        + f"; {len(policy.default)} milestones, all pending business confirmation"
    )


async def elmich_packaging(migrator: AsyncEngine, app: AsyncEngine) -> None:
    policy = load_supply_chain_packaging_policy(ELMICH_PACKAGING)
    owner = _context(await _user_id(migrator, PROCESS_OWNER)).model_copy(
        update={
            "roles": frozenset({"sc_process_admin"}),
            "scopes": frozenset({ACTION_DUTIES_WRITE}),
        }
    )
    await SetPackagingPolicyOverride(
        policy_override_repo=SqlPolicyOverrideRepository(async_sessionmaker(app)),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(owner, policy)
    print(
        "elmich packaging override written for tenant Alpha: require_pre_production_test="
        f"{policy.require_pre_production_test}"
    )


async def elmich_step_preparation(migrator: AsyncEngine, app: AsyncEngine) -> None:
    policy = load_supply_chain_step_preparation(ELMICH_STEP_PREPARATION)
    owner = _context(await _user_id(migrator, PROCESS_OWNER)).model_copy(
        update={
            "roles": frozenset({"sc_process_admin"}),
            "scopes": frozenset({ACTION_DUTIES_WRITE}),
        }
    )
    await SetStepPreparationPolicyOverride(
        policy_override_repo=SqlPolicyOverrideRepository(async_sessionmaker(app)),
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=SystemClock(),
    ).handle(owner, policy)
    print(
        "elmich step preparation written for tenant Alpha: "
        + ", ".join(f"{s.state.value} -> {s.action.value}" for s in policy.steps)
    )


# A second Alpha workspace, only for the browser suite (`e2e-fixtures`).
E2E_WS = uuid.uuid5(ALPHA, "e2e-second-workspace")
E2E_BOD = "dev|khanh.ngo"
E2E_CHAT = "e2e-chat-khanh"


async def e2e_fixtures(migrator: AsyncEngine) -> None:
    bod = await _user_id(migrator, E2E_BOD)
    async with migrator.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO platform.workspaces (id, tenant_id, slug, name)"
                " VALUES (:id, :tenant, 'kho-hung-yen', 'Kho Hưng Yên')"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"id": E2E_WS, "tenant": ALPHA},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO platform.memberships"
                " (id, tenant_id, workspace_id, user_id, role_keys, department)"
                " VALUES (:id, :tenant, :workspace, :user, CAST(:roles AS jsonb), 'bgd')"
                " ON CONFLICT ON CONSTRAINT uq_memberships_scope_user DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": ALPHA,
                "workspace": E2E_WS,
                "user": bod,
                "roles": json.dumps(["approver", "sc_bod"]),
            },
        )
        linked = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM platform.external_identities"
                " WHERE user_id = :u AND provider = 'zalo'"
            ),
            {"u": bod},
        )
        if not linked:
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.external_identities"
                    " (id, user_id, issuer, subject, provider)"
                    " VALUES (:id, :u, 'zalo', :chat, 'zalo')"
                ),
                {"id": uuid.uuid4(), "u": bod, "chat": E2E_CHAT},
            )
    print(f"e2e fixtures: {E2E_BOD} linked to Zalo, member of workspace Kho Hưng Yên")


_E2E_PREFIX = "E2E-"


async def e2e_cleanup(migrator: AsyncEngine, code: str | None) -> None:
    if code is not None and not code.startswith(_E2E_PREFIX):
        sys.exit(f"refusing to clean up {code!r}: only {_E2E_PREFIX}… cases are the suite's")
    async with migrator.begin() as conn:
        cases = (
            await conn.execute(
                sa.text(
                    "SELECT c.id, c.proposal_code,"
                    " EXISTS (SELECT 1 FROM supply_chain.po_cases p"
                    "         WHERE p.product_dev_case_id = c.id) AS ordered"
                    " FROM supply_chain.product_dev_cases c"
                    " WHERE c.tenant_id = :t AND c.proposal_code LIKE :pattern"
                ),
                {
                    "t": ALPHA,
                    "pattern": code if code is not None else f"{_E2E_PREFIX}%",
                },
            )
        ).all()
        kept = [row.proposal_code for row in cases if row.ordered]
        ids = [row.id for row in cases if not row.ordered]
        if ids:
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.approval_requests WHERE tenant_id = :t"
                    " AND payload ->> 'product_dev_case_id' = ANY(:ids)"
                ),
                {"t": ALPHA, "ids": [str(i) for i in ids]},
            )
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.notifications WHERE tenant_id = :t"
                    " AND split_part(link, '?', 1) = ANY(:links)"
                ),
                {"t": ALPHA, "links": [f"/supply-chain/product-cases/{i}" for i in ids]},
            )
            await conn.execute(
                sa.text("DELETE FROM supply_chain.product_dev_cases WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
    print(f"e2e cleanup: {len(ids)} product case(s) removed")
    if kept:
        print(f"kept, ordered (a PO case holds each): {', '.join(sorted(kept))}")


async def main(argv: list[str]) -> None:
    migrator_url, app_url = _urls()
    migrator = create_async_engine(migrator_url, poolclass=NullPool)
    app = create_async_engine(app_url, poolclass=NullPool)
    try:
        if argv[:1] == ["seed"]:
            await seed(migrator, app, migrator_url)
        elif argv[:1] == ["supplier-update"] and len(argv) == 2:
            await supplier_update(migrator, app, argv[1])
        elif argv == ["elmich-sla"]:
            await elmich_sla(migrator, app)
        elif argv == ["elmich-packaging"]:
            await elmich_packaging(migrator, app)
        elif argv == ["elmich-step-preparation"]:
            await elmich_step_preparation(migrator, app)
        elif argv == ["e2e-fixtures"]:
            await e2e_fixtures(migrator)
        elif argv[:1] == ["e2e-cleanup"] and len(argv) <= 2:
            await e2e_cleanup(migrator, argv[1] if len(argv) == 2 else None)
        else:
            sys.exit(__doc__)
    finally:
        await migrator.dispose()
        await app.dispose()


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    asyncio.run(main(sys.argv[1:]))
