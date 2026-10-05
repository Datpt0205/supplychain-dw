"""Seed the local dev database with Supply Chain demo data, for trying it in a browser.

Usage (reads `.env`; `make` exports it, a plain shell needs `set -a; source .env`):
    uv run python scripts/seed_supply_chain_demo.py seed
    uv run python scripts/seed_supply_chain_demo.py supplier-update PO-DEMO-001

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
- four PO cases, backdated so each signal is already due:
    - PO-DEMO-001: opened 25 hours ago, no supplier update (a reminder);
    - PO-DEMO-002: opened 73 hours ago, no supplier update (an escalation);
    - PO-DEMO-003: waiting for its deposit for 49 hours against a 1-day SLA
      (an SLA breach), with a fresh supplier update;
    - PO-DEMO-004: opened now with a fresh supplier update (nothing due).

`supplier-update <PO>` records a supplier update on that case now, as An, so
the next sweep resolves its follow-up.

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

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dw_kernel.ids import TenantId, WorkspaceId
from dw_platform.application.access_context import AccessContext
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.adapters.persistence.po_case_repository import SqlPOCaseRepository
from dw_supply_chain.adapters.persistence.supplier_update_repository import (
    SqlSupplierUpdateRepository,
)
from dw_supply_chain.domain.po_case import POCase, POCaseId
from dw_supply_chain.domain.supplier_update import (
    SupplierEventType,
    SupplierUpdate,
    SupplierUpdateExtraction,
    SupplierUpdateId,
)

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


async def main(argv: list[str]) -> None:
    migrator_url, app_url = _urls()
    migrator = create_async_engine(migrator_url, poolclass=NullPool)
    app = create_async_engine(app_url, poolclass=NullPool)
    try:
        if argv[:1] == ["seed"]:
            await seed(migrator, app, migrator_url)
        elif argv[:1] == ["supplier-update"] and len(argv) == 2:
            await supplier_update(migrator, app, argv[1])
        else:
            sys.exit(__doc__)
    finally:
        await migrator.dispose()
        await app.dispose()


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    asyncio.run(main(sys.argv[1:]))
