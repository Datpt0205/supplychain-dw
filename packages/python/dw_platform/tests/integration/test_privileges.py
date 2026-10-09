"""Integration: what the application role may and may not do.

A schema with no grants passes every health check and fails on the first real
query — the probe runs ``SELECT 1``, which needs no table privilege. That is
exactly how a baseline built with ``pg_dump --no-privileges`` shipped once:
green everywhere, and an application that could not read a row.

So the privilege model is asserted, not assumed. Three claims:

* ``dw_app`` can read and write the tables it serves;
* ``dw_app`` CANNOT rewrite the audit log — append-only is a grant, not a
  convention somebody could code around;
* ``dw_app`` cannot touch the provisioning record, which belongs to a different
  role making a different decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from pg_harness import DatabaseUrls
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from test_rls_coverage import tenant_schemas

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_engine(db_urls: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """A connection as the runtime role, not as the migrator.

    The migrator holds BYPASSRLS and owns every object, so a test that connects
    as the migrator proves nothing about what the application is allowed to do.
    """
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_the_application_can_read_the_tables_it_serves(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        for table in (
            "platform.tenants",
            "platform.workspaces",
            "platform.users",
            "platform.memberships",
            "knowledge.documents",
            "memory.items",
        ):
            # The count is irrelevant; being allowed to ask is the assertion.
            await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))


async def test_the_application_can_enter_every_tenant_schema(db_urls: DatabaseUrls) -> None:
    """Without USAGE on its schema no table grant is reachable: the first real
    query fails with "permission denied for schema" while `SELECT 1` stays green.

    The schemas come from the catalog — `tenant_schemas`, the same discovery the
    RLS coverage test uses — not from a list here. `0001_platform_grants.sql`
    grants USAGE on the schemas that existed when it was written; a schema added
    later needs its own grant, and a hand-kept list would be blind to exactly
    that schema.
    """
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            schemas = await tenant_schemas(conn)
            result = await conn.execute(
                sa.text(
                    "SELECT s FROM unnest(CAST(:schemas AS text[])) AS s"
                    " WHERE NOT has_schema_privilege('dw_app', s, 'USAGE')"
                    " ORDER BY s"
                ),
                {"schemas": schemas},
            )
            unreachable = result.scalars().all()
    finally:
        await migrator.dispose()
    assert unreachable == [], f"dw_app has no USAGE on tenant schemas: {unreachable}"


async def test_the_application_cannot_rewrite_the_audit_log(app_engine: AsyncEngine) -> None:
    """Append-only, enforced by the database.

    An UPDATE that the code would never issue is still an UPDATE the database
    must refuse: credentials reach further than the code that was reviewed.
    """
    async with app_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("UPDATE platform.audit_events SET action = 'x'"))
        await conn.rollback()
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("DELETE FROM platform.audit_events"))


async def test_a_decision_cannot_be_rewritten(db_urls: DatabaseUrls) -> None:
    """Written once, rewritten by nothing (migration ecb47f78702c): who decided
    is also on the append-only audit log, and this copy must not disagree.
    DELETE stays, for offboarding's purge."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            held = {
                verb: await conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege('dw_app', 'platform.approval_decisions', :v)"
                    ),
                    {"v": verb},
                )
                for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
            }
    finally:
        await migrator.dispose()
    assert held == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}


async def test_the_application_cannot_update_a_decision(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("UPDATE platform.approval_decisions SET comment = 'x'"))


_CATALOGUE = ("platform.roles", "platform.permission_sets")


async def test_the_application_cannot_rewrite_the_role_catalogue(app_engine: AsyncEngine) -> None:
    """Which scopes a membership carries changes in migrations alone.

    No application code writes these tables, so a write under application
    credentials could only be someone widening a role past a
    separation-of-duty rule (migration 648e2f7c3edb).
    """
    async with app_engine.connect() as conn:
        for table in _CATALOGUE:
            await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))
            await conn.rollback()
            for statement in (
                f"UPDATE {table} SET scopes = scopes || '[\"platform.admin\"]'::jsonb",
                f"DELETE FROM {table}",
                f"INSERT INTO {table} (key, name, scopes) VALUES ('probe', 'Probe', '[]')",
            ):
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(sa.text(statement))
                await conn.rollback()


async def test_no_application_role_can_write_the_role_catalogue(db_urls: DatabaseUrls) -> None:
    """Asked of the catalog, so every verb and every runtime role is covered,
    `dw_provisioner` included: it is granted SELECT on roles and no more."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            writable = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT r.rolname, t.tbl, v.verb
                        FROM pg_roles r
                        CROSS JOIN unnest(CAST(:tables AS text[])) AS t(tbl)
                        CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'])
                            AS v(verb)
                        WHERE r.rolname IN ('dw_app', 'dw_provisioner', 'dw_agent_ro')
                          AND has_table_privilege(r.rolname, t.tbl, v.verb)
                        """
                    ),
                    {"tables": list(_CATALOGUE)},
                )
            ).all()
    finally:
        await migrator.dispose()
    assert writable == []


async def test_the_application_cannot_read_the_provisioning_record(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.connect() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(sa.text("SELECT count(*) FROM platform.provisioning_audit"))


async def test_a_table_added_later_is_readable_without_a_new_grant(
    app_engine: AsyncEngine, db_urls: DatabaseUrls
) -> None:
    """Default privileges carry, so "somebody forgot the GRANT" cannot ship.

    Written as a real table created by the migrator, because that is the case
    that goes wrong: every later migration adds tables, and each one relying on
    a hand-written grant is one release away from the failure above.
    """
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE platform.grant_probe (id uuid PRIMARY KEY)"))
        async with app_engine.connect() as conn:
            await conn.execute(sa.text("SELECT count(*) FROM platform.grant_probe"))
        async with migrator.begin() as conn:
            await conn.execute(sa.text("DROP TABLE platform.grant_probe"))
    finally:
        await migrator.dispose()


async def test_the_application_may_only_mark_a_link_nonce_used(db_urls: DatabaseUrls) -> None:
    """`platform.channel_link_nonces` (migration 02930a73bbdf): `dw_app` issues,
    consumes and prunes nonces, and the consume may set `used_at` and nothing
    else — never move a nonce to another user or stretch its expiry. Asked of the
    catalog, so a migration that dropped the column-level grant goes red here."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_table_privilege('dw_app',"
                            " 'platform.channel_link_nonces', :verb)"
                        ),
                        {"verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege('dw_app',"
                            " 'platform.channel_link_nonces', :col, :verb)"
                        ),
                        {"col": name, "verb": verb},
                    )
                )

            assert await table("SELECT")
            assert await table("INSERT")
            assert await table("DELETE")
            assert not await table("UPDATE")  # no table-wide UPDATE
            assert not await table("TRUNCATE")
            assert await column("used_at", "UPDATE")
            for name in ("jti", "channel", "user_id", "expires_at", "created_at"):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()


async def test_the_application_may_only_settle_an_inbound_message_and_choose_a_workspace(
    db_urls: DatabaseUrls,
) -> None:
    """Migration 9f2becb1bf80. `channel_inbound_messages`: `dw_app` claims
    (INSERT), reads, settles (UPDATE of `outcome` only) and prunes (DELETE) —
    it can never move a claimed id to another user or re-date it.
    `channel_preferences`: `dw_app` reads, inserts and updates the chosen
    tenant/workspace, never the owning `user_id`, and never deletes (the row
    goes with the membership, by cascade). Asked of the catalog."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :verb)"),
                        {"t": f"platform.{name}", "verb": verb},
                    )
                )

            async def column(name: str, col: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_column_privilege('dw_app', :t, :col, :verb)"),
                        {"t": f"platform.{name}", "col": col, "verb": verb},
                    )
                )

            inbound = "channel_inbound_messages"
            for verb in ("SELECT", "INSERT", "DELETE"):
                assert await table(inbound, verb), verb
            assert not await table(inbound, "UPDATE")
            assert not await table(inbound, "TRUNCATE")
            assert await column(inbound, "outcome", "UPDATE")
            for col in ("channel", "external_message_id", "user_id", "received_at"):
                assert not await column(inbound, col, "UPDATE"), col

            chosen = "channel_preferences"
            assert await table(chosen, "SELECT")
            assert await table(chosen, "INSERT")
            for verb in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await table(chosen, verb), verb
            assert await column(chosen, "tenant_id", "UPDATE")
            assert await column(chosen, "workspace_id", "UPDATE")
            assert not await column(chosen, "user_id", "UPDATE")

            # Migration of channels Z3: the API queues a webhook
            # update, the worker's drain takes (deletes) it; nothing edits one.
            queued = "channel_inbound_updates"
            for verb in ("SELECT", "INSERT", "DELETE"):
                assert await table(queued, verb), verb
            for verb in ("UPDATE", "TRUNCATE"):
                assert not await table(queued, verb), verb
    finally:
        await migrator.dispose()


async def test_the_application_may_only_record_views_and_spend_or_revoke_codes(
    db_urls: DatabaseUrls,
) -> None:
    """Migration e399be8c0a2d. `approval_view_receipts`: `dw_app` reads and
    inserts, never edits or deletes a receipt. `approval_decision_codes`: reads,
    inserts, and updates only `used_at`, `revoked_at`, `revoked_reason` and
    `failed_attempts` — never the hash, the comment, the owner, the approval or
    the expiry — and deletes nothing; the sweep runs through
    `prune_approval_decision_codes()`, which it may execute. Asked of the
    catalog."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :verb)"),
                        {"t": f"platform.{name}", "verb": verb},
                    )
                )

            async def column(name: str, col: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_column_privilege('dw_app', :t, :col, :verb)"),
                        {"t": f"platform.{name}", "col": col, "verb": verb},
                    )
                )

            receipts = "approval_view_receipts"
            assert await table(receipts, "SELECT")
            assert await table(receipts, "INSERT")
            for verb in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await table(receipts, verb), verb

            codes = "approval_decision_codes"
            assert await table(codes, "SELECT")
            assert await table(codes, "INSERT")
            for verb in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await table(codes, verb), verb
            for col in ("used_at", "revoked_at", "revoked_reason", "failed_attempts"):
                assert await column(codes, col, "UPDATE"), col
            for col in (
                "id",
                "tenant_id",
                "workspace_id",
                "approval_id",
                "user_id",
                "receipt_id",
                "code_hash",
                "comment",
                "expires_at",
                "created_at",
            ):
                assert not await column(codes, col, "UPDATE"), col
            assert await conn.scalar(
                sa.text(
                    "SELECT has_function_privilege("
                    "'dw_app', 'platform.prune_approval_decision_codes()', 'EXECUTE')"
                )
            )
    finally:
        await migrator.dispose()


async def test_the_application_may_only_record_a_decision_on_an_approval(
    db_urls: DatabaseUrls,
) -> None:
    """`platform.approval_requests` (migration 36dabf47619c, or its twin
    5d3965984679 here): a decision writes `status`, `decided_at` and `version`,
    and nothing else may move. Above all `required_scope`, the stamp of who may
    decide (ADR 0004). Asked of the
    catalog, so a later blanket GRANT that restored table-wide UPDATE goes red."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_table_privilege('dw_app',"
                            " 'platform.approval_requests', :verb)"
                        ),
                        {"verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege('dw_app',"
                            " 'platform.approval_requests', :col, :verb)"
                        ),
                        {"col": name, "verb": verb},
                    )
                )

            assert await table("SELECT")
            assert await table("INSERT")
            # The offboarding lane deletes every table dw_app may DELETE.
            assert await table("DELETE")
            assert not await table("UPDATE")  # no table-wide UPDATE
            for name in ("status", "decided_at", "version"):
                assert await column(name, "UPDATE"), name
            for name in (
                "required_scope",
                "approval_type",
                "requested_by",
                "payload",
                "tenant_id",
                "workspace_id",
                "run_id",
            ):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()


async def test_the_application_may_only_close_a_sample_round(db_urls: DatabaseUrls) -> None:
    """`supply_chain.product_sample_rounds` (migration 59e69efdfa37): a round
    is opened by INSERT and closed by writing its result, its evaluation and
    who closed it when; nothing else may move, and nothing is deleted by hand
    (the case's cascade is the only way out). Asked of the catalog, so a later
    blanket GRANT that restored table-wide UPDATE or DELETE goes red."""
    table_name = "supply_chain.product_sample_rounds"
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :verb)"),
                        {"t": table_name, "verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_column_privilege('dw_app', :t, :col, :verb)"),
                        {"t": table_name, "col": name, "verb": verb},
                    )
                )

            assert await table("SELECT")
            assert await table("INSERT")
            assert not await table("UPDATE")
            assert not await table("DELETE")
            assert not await table("TRUNCATE")
            for name in ("result", "evaluation_document_id", "closed_at", "closed_by"):
                assert await column(name, "UPDATE"), name
            for name in (
                "id",
                "tenant_id",
                "workspace_id",
                "product_dev_case_id",
                "round_no",
                "opened_at",
                "opened_by",
            ):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()


async def test_the_application_may_only_change_a_proposal_drafts_own_state(
    db_urls: DatabaseUrls,
) -> None:
    """`supply_chain.proposal_drafts` (migration d4048e50d4a3): `dw_app` reads,
    creates and deletes a chat proposal draft (the case consumes it; the
    retention lane sweeps it), and updates only its content, versions and
    expiry — never whose it is, where it lives, or the channel it came from.
    Asked of the catalog, so a later blanket GRANT goes red."""
    table_name = "supply_chain.proposal_drafts"
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :verb)"),
                        {"t": table_name, "verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_column_privilege('dw_app', :t, :col, :verb)"),
                        {"t": table_name, "col": name, "verb": verb},
                    )
                )

            for verb in ("SELECT", "INSERT", "DELETE"):
                assert await table(verb), verb
            assert not await table("UPDATE")
            assert not await table("TRUNCATE")
            for name in ("draft", "draft_version", "summarized_version", "expires_at"):
                assert await column(name, "UPDATE"), name
            for name in ("id", "tenant_id", "workspace_id", "user_id", "channel", "created_at"):
                assert not await column(name, "UPDATE"), name
    finally:
        await migrator.dispose()


async def test_the_application_issues_item_codes_and_adds_or_removes_skus_only(
    db_urls: DatabaseUrls,
) -> None:
    """`supply_chain.item_codes` and `supply_chain.skus` (migration
    76bd1b5fc546, step 9): an item code is issued (INSERT) and corrected
    (`code` only), never deleted but by its case's cascade; a SKU is added and
    removed, never edited. Asked of the catalog, so a later blanket GRANT
    goes red."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :verb)"),
                        {"t": f"supply_chain.{name}", "verb": verb},
                    )
                )

            async def column(name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_column_privilege('dw_app', :t, :col, :verb)"),
                        {"t": "supply_chain.item_codes", "col": name, "verb": verb},
                    )
                )

            for verb in ("SELECT", "INSERT"):
                assert await table("item_codes", verb), verb
            for verb in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await table("item_codes", verb), verb
            assert await column("code", "UPDATE")
            for name in ("id", "tenant_id", "workspace_id", "product_dev_case_id", "issued_by"):
                assert not await column(name, "UPDATE"), name
            for verb in ("SELECT", "INSERT", "DELETE"):
                assert await table("skus", verb), verb
            for verb in ("UPDATE", "TRUNCATE"):
                assert not await table("skus", verb), verb
    finally:
        await migrator.dispose()


async def test_support_access_privileges(db_urls: DatabaseUrls) -> None:
    """Migration af8ee878b4ab (ADR 0024). `support_staff`: `dw_app` reads, the
    provisioner reads, adds and removes. `support_grants`: `dw_app` reads,
    inserts the customer's columns and updates only the columns of the
    customer's steps (approve, reject, revoke), never the assignment and never
    a DELETE; the provisioner reads and updates only the assignment, and may
    append to the tenant's audit log. Asked of the catalog."""
    customer_update = (
        "status",
        "granted_by",
        "granted_at",
        "rejected_by",
        "rejected_at",
        "reject_reason",
        "revoked_by",
        "revoked_at",
    )
    assignment = ("staff_user_id", "assigned_by", "activated_at", "expires_at")
    stamped = ("tenant_id", "workspace_id", "scopes", "scope_set_key", "requested_by", "code")
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:

            async def table(role: str, name: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text("SELECT has_table_privilege(:r, :t, :v)"),
                        {"r": role, "t": f"platform.{name}", "v": verb},
                    )
                )

            async def column(role: str, col: str, verb: str) -> bool:
                return bool(
                    await conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege(:r, 'platform.support_grants', :c, :v)"
                        ),
                        {"r": role, "c": col, "v": verb},
                    )
                )

            assert await table("dw_app", "support_staff", "SELECT")
            for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await table("dw_app", "support_staff", verb), verb
            assert await table("dw_app", "support_grants", "SELECT")
            for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await table("dw_app", "support_grants", verb), verb
            for col in customer_update:
                assert await column("dw_app", col, "UPDATE"), col
            for col in (*assignment, *stamped):
                assert not await column("dw_app", col, "UPDATE"), col
            for col in assignment:
                assert not await column("dw_app", col, "INSERT"), col

            for verb in ("SELECT", "INSERT", "DELETE"):
                assert await table("dw_provisioner", "support_staff", verb), verb
            assert await table("dw_provisioner", "support_grants", "SELECT")
            for verb in ("INSERT", "UPDATE", "DELETE"):
                assert not await table("dw_provisioner", "support_grants", verb), verb
            for col in assignment:
                assert await column("dw_provisioner", col, "UPDATE"), col
            for col in (*customer_update[1:], *stamped):
                assert not await column("dw_provisioner", col, "UPDATE"), col
            assert await table("dw_provisioner", "audit_events", "INSERT")
            for verb in ("UPDATE", "DELETE"):
                assert not await table("dw_provisioner", "audit_events", verb), verb
    finally:
        await migrator.dispose()


async def test_the_application_reads_and_creates_suppliers_and_never_renames_one(
    db_urls: DatabaseUrls,
) -> None:
    """`supply_chain.suppliers` (migration a0035e9faf32): `dw_app` reads and
    creates a supplier, and may DELETE only so the offboarding purge empties
    the table (every case RESTRICTs its supplier); it never UPDATEs one, so no
    code path renames a supplier until a reviewed one is added. Asked of the
    catalog, so a later blanket GRANT goes red."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            for verb, held in (
                ("SELECT", True),
                ("INSERT", True),
                ("DELETE", True),
                ("UPDATE", False),
                ("TRUNCATE", False),
            ):
                granted = await conn.scalar(
                    sa.text("SELECT has_table_privilege('dw_app', 'supply_chain.suppliers', :v)"),
                    {"v": verb},
                )
                assert bool(granted) is held, verb
    finally:
        await migrator.dispose()


async def test_commercial_tables_are_append_only_and_a_line_price_is_the_one_new_update(
    db_urls: DatabaseUrls,
) -> None:
    """Migration 82221a867e62 (ADR 0026): `dw_app` reads and inserts BM04
    profiles, payments, supplier contacts and bank accounts, and never edits or
    deletes one (each leaves only with its case or supplier, by cascade). On
    `po_case_lines` it may UPDATE `unit_price` beside `quantity`, nothing else.
    Asked of the catalog, so a later blanket GRANT goes red."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            for table in (
                "product_profiles",
                "po_payments",
                "supplier_contacts",
                "supplier_bank_accounts",
            ):
                for verb, held in (
                    ("SELECT", True),
                    ("INSERT", True),
                    ("UPDATE", False),
                    ("DELETE", False),
                    ("TRUNCATE", False),
                ):
                    granted = await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :v)"),
                        {"t": f"supply_chain.{table}", "v": verb},
                    )
                    assert bool(granted) is held, (table, verb)
            for column, held in (("unit_price", True), ("quantity", True), ("sku_id", False)):
                granted = await conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege('dw_app', 'supply_chain.po_case_lines',"
                        " :c, 'UPDATE')"
                    ),
                    {"c": column},
                )
                assert bool(granted) is held, column
    finally:
        await migrator.dispose()


async def test_document_extractions_are_append_only_and_the_queue_is_executable(
    db_urls: DatabaseUrls,
) -> None:
    """Migration e21dc10d13b5: `dw_app` reads and inserts readings and never
    edits or deletes one; it may execute the definer function that hands the
    extraction lane document ids, and PUBLIC may not."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            for verb, held in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
                ("TRUNCATE", False),
            ):
                granted = await conn.scalar(
                    sa.text(
                        "SELECT has_table_privilege('dw_app',"
                        " 'supply_chain.document_extractions', :v)"
                    ),
                    {"v": verb},
                )
                assert bool(granted) is held, verb
            fn = "supply_chain.documents_awaiting_extraction(jsonb, integer)"
            assert await conn.scalar(
                sa.text("SELECT has_function_privilege('dw_app', :f, 'EXECUTE')"), {"f": fn}
            )
            assert not await conn.scalar(
                sa.text("SELECT has_function_privilege('public', :f, 'EXECUTE')"), {"f": fn}
            )
    finally:
        await migrator.dispose()


async def test_drafts_decisions_and_template_overrides_are_append_only(
    db_urls: DatabaseUrls,
) -> None:
    """Migration cbebad572558: `dw_app` reads and inserts drafts, their
    decisions and a tenant's template versions, and never edits or deletes
    one (each leaves with its case, or its tenant, by cascade)."""
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with migrator.connect() as conn:
            for table in ("document_drafts", "document_draft_decisions", "doc_template_overrides"):
                for verb, held in (
                    ("SELECT", True),
                    ("INSERT", True),
                    ("UPDATE", False),
                    ("DELETE", False),
                    ("TRUNCATE", False),
                ):
                    granted = await conn.scalar(
                        sa.text("SELECT has_table_privilege('dw_app', :t, :v)"),
                        {"t": f"supply_chain.{table}", "v": verb},
                    )
                    assert bool(granted) is held, (table, verb)
    finally:
        await migrator.dispose()
