"""Tenant offboarding: export then purge every tenant-scoped table.

Discovered from the catalog rather than a hand-maintained list — the same
query `test_migration_and_rls.py`'s own RLS-coverage test already uses
(`pg_policies` filtered to `tenant_isolation_%`), so a table added by a later
migration — even from a bounded context this repo does not ship — is covered
the day its policy is created, not the day someone remembers to add it here.
Hand-maintained lists of tenant tables have drifted in this repo before
(`platform.tenant_scoped_tables` in `tables.py` is dead code nobody reads,
found while building this).

`export_rows`/`purge_rows` run entirely under `app.tenant_id` for the one
tenant they target — the ordinary RLS mechanism every tenant-scoped query
already uses — plus `app.workspace_scope = 'tenant'`. A context may narrow its
tables by workspace as well (`tenant AND (workspace OR app.workspace_scope =
'tenant')`); under `app.tenant_id` alone such a table reads zero rows, so the
export would miss them silently, the purge would delete none, and the purge of
`platform.workspaces` would then CASCADE them away unexported. This class is the
only place that sets the scope, per transaction; `test_rls_coverage.py` keeps
every policy that reads it inside a tenant clause.

`claim_requested` is the one exception to running under one tenant: nothing
tells the worker which tenant has a request waiting until it asks, and asking is
itself the cross-tenant read `app.tenant_id` scoping exists to prevent — so
it runs under `app.worker_drain`, like the retention sweep, but only for that
one query. Migration `dd1db8ca43a2`.

What this does NOT do: touch Qdrant (either collection: knowledge chunks or
memory vectors) or object storage. Both live behind ports
this package may not import (import-linter's "Vector/object-storage SDKs only
inside knowledge adapters"). The worker's offboarding lane — the composition
root, which may import every concrete adapter — orchestrates those
separately, calling `export_rows`/`purge_rows` here for the Postgres half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = ["ExportedTable", "SqlTenantOffboarding"]

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")
# Every workspace of the one tenant, for this transaction only (`true`): a pooled
# connection must not carry the scope into whoever borrows it next.
_SET_TENANT_ALL_WORKSPACES = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true),"
    "       set_config('app.workspace_scope', 'tenant', true)"
)
_SET_DRAIN = text("SELECT set_config('app.worker_drain', 'on', true)")

# How long an in-progress request goes untouched before `claim_requested`
# treats it as abandoned by a dead worker rather than owned by a live one.
# Comfortably above how long one tenant's export+purge should ever take.
_STALE_CLAIM_MINUTES = 30

# Joined from `pg_policy` and privileges asked by oid, not by name through
# the `pg_policies` view: the planner is free to evaluate a name-based
# `has_table_privilege` on every `pg_class` row before the join narrows them
# to policies, and a name in `pg_toast` is "permission denied for schema" to
# dw_app. Measured: adding three tables changed the plan and this query
# failed on every call. An oid needs no schema lookup.
_CATALOG_COLUMNS = (
    "SELECT DISTINCT n.nspname AS schemaname, c.relname AS tablename"
    " FROM pg_policy pol"
    " JOIN pg_class c ON c.oid = pol.polrelid"
    " JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE pol.polname LIKE 'tenant_isolation_%'"
    "   AND has_table_privilege('dw_app', c.oid, 'SELECT')"
    # platform.tenants carries a tenant_isolation_% policy (it IS the tenant,
    # keyed on `id`) but no `tenant_id` column of its own — the generic
    # `WHERE tenant_id = :t` this class runs would fail on it with "column
    # does not exist", found while writing test_offboarding.py. Filtering on
    # the column actually existing excludes it (and anything shaped like it
    # later) without a hand-maintained exception list.
    "   AND EXISTS ("
    "     SELECT 1 FROM information_schema.columns ic"
    "     WHERE ic.table_schema = n.nspname AND ic.table_name = c.relname"
    "       AND ic.column_name = 'tenant_id'"
    "   )"
)

# Every table this class may SELECT from a tenant to export it.
_CATALOG_EXPORTABLE = text(_CATALOG_COLUMNS + " ORDER BY schemaname, tablename")

# Only the ones `dw_app` may also DELETE from. `platform.audit_events` is
# exportable but not this: `0001_platform_grants.sql` revokes UPDATE/DELETE
# from `dw_app` there on purpose (append-only; the audit term is
# `retention@1.6.0.yaml`'s own decision, not offboarding's to shorten). Found
# by running this against a real database rather than assumed — the same
# revoke could apply to a table added later, and asking `has_table_privilege`
# instead of hand-naming `audit_events` catches that one too.
_CATALOG_PURGEABLE = text(
    _CATALOG_COLUMNS + "   AND has_table_privilege('dw_app', c.oid, 'DELETE')"
    " ORDER BY schemaname, tablename"
)

# FK RESTRICT edges among tenant-scoped tables, checked against the catalog
# (not assumed) while designing this — see the ops-hardening plan. Purge
# order: earlier entries first, because each RESTRICTs the one after it.
# memory.items also cascades memory.item_evidence, which is why that table
# needs no entry of its own here.
_ORDERED_FIRST: tuple[tuple[str, str], ...] = (
    ("memory", "items"),  # RESTRICTs platform.worker_runs
    ("knowledge", "evidence"),  # memory.item_evidence (now gone) RESTRICTed this
    ("knowledge", "chunks"),  # evidence (now gone) RESTRICTed this
    ("knowledge", "documents"),  # evidence (now gone) RESTRICTed this too
    ("platform", "worker_runs"),  # items (now gone) RESTRICTed this
)

# This row IS the job — purging it out from under the worker running this
# pass is not a table this pass owns clearing.
_NEVER_PURGE = frozenset({("platform", "tenant_offboarding_requests")})

# A real schema/table identifier from Postgres's own catalog never needs
# quoting and never contains anything this does not match. Checked, not
# assumed: the alternative is string-formatting a table name from a query
# result straight into SQL, and this is what stands between that and being
# indistinguishable from injection.
_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quoted(schema: str, table: str) -> str:
    for identifier in (schema, table):
        if not _SAFE_IDENTIFIER.fullmatch(identifier):
            raise ValueError(f"refusing an unsafe catalog identifier: {identifier!r}")
    return f'"{schema}"."{table}"'


@dataclass(frozen=True)
class ExportedTable:
    schema: str
    table: str
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class SqlTenantOffboarding:
    session_factory: async_sessionmaker[AsyncSession]

    async def _exportable_tables(self, session: AsyncSession) -> list[tuple[str, str]]:
        rows = (await session.execute(_CATALOG_EXPORTABLE)).all()
        return [
            (row.schemaname, row.tablename)
            for row in rows
            if (row.schemaname, row.tablename) not in _NEVER_PURGE
        ]

    async def _purgeable_tables(self, session: AsyncSession) -> list[tuple[str, str]]:
        rows = (await session.execute(_CATALOG_PURGEABLE)).all()
        return [
            (row.schemaname, row.tablename)
            for row in rows
            if (row.schemaname, row.tablename) not in _NEVER_PURGE
        ]

    async def export_rows(self, tenant_id: UUID) -> list[ExportedTable]:
        exported: list[ExportedTable] = []
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT_ALL_WORKSPACES, {"tenant_id": str(tenant_id)})
            for schema, table in await self._exportable_tables(session):
                result = await session.execute(
                    # B608 is a false positive here: the identifiers come from the catalog
                    # (`_CATALOG_*`), never from a caller, and `_quoted` quotes them; the
                    # tenant is a bind parameter. Same for the DELETE in `purge_rows`.
                    text(f"SELECT * FROM {_quoted(schema, table)} WHERE tenant_id = :t"),  # nosec B608
                    {"t": str(tenant_id)},
                )
                exported.append(
                    ExportedTable(
                        schema=schema,
                        table=table,
                        rows=[dict(row._mapping) for row in result],
                    )
                )
        return exported

    async def purge_rows(self, tenant_id: UUID) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT_ALL_WORKSPACES, {"tenant_id": str(tenant_id)})
            await self._record_purge_audit(session, tenant_id)
            tables = await self._purgeable_tables(session)
            ordered_first = [t for t in _ORDERED_FIRST if t in tables]
            rest = [t for t in tables if t not in _ORDERED_FIRST]
            for schema, table in [*ordered_first, *rest]:
                await session.execute(
                    text(f"DELETE FROM {_quoted(schema, table)} WHERE tenant_id = :t"),  # nosec B608
                    {"t": str(tenant_id)},
                )

    async def _record_purge_audit(self, session: AsyncSession, tenant_id: UUID) -> None:
        """A row in `platform.audit_events` for the one action here that
        cannot be undone — written first, in the same transaction as the
        first delete, so the record survives even if this pass crashes right
        after. Safe to be the first delete-adjacent write: `audit_events`
        is exportable but never purgeable (`dw_app` has no DELETE there — see
        `_CATALOG_PURGEABLE`), so this row outlives every other table this
        pass empties, for as long as `retention@1.6.0.yaml`'s own term says.

        `workspace_id`/`actor_id` are NOT NULL on `audit_events` and this
        pass has neither on hand directly, so both are looked up: any one of
        the tenant's own workspaces, and the operator who requested this
        (`tenant_offboarding_requests.requested_by`). If either is missing —
        a tenant with no workspace, or `purge_rows` called with no request on
        file, both edge cases rather than the real path (the worker lane
        always claims a request first) — the audit write is skipped rather
        than failing the purge: the deletion itself is the obligation this
        method exists to fulfil, and a missing "who asked" is a worse outcome
        to trade it against than a missing audit row for an edge case that
        should not occur.
        """
        workspace_id = await session.scalar(
            text("SELECT id FROM platform.workspaces WHERE tenant_id = :t LIMIT 1"),
            {"t": str(tenant_id)},
        )
        requested_by = await session.scalar(
            text(
                "SELECT requested_by FROM platform.tenant_offboarding_requests"
                " WHERE tenant_id = :t ORDER BY requested_at DESC LIMIT 1"
            ),
            {"t": str(tenant_id)},
        )
        if workspace_id is None or requested_by is None:
            return
        await session.execute(
            text(
                "INSERT INTO platform.audit_events"
                " (id, tenant_id, workspace_id, actor_id, action, resource_type,"
                "  resource_id, details, occurred_at)"
                " VALUES (gen_random_uuid(), :t, :w, :actor, 'tenant.offboarding.purged',"
                "  'tenant', :resource_id, '{}', now())"
            ),
            {
                "t": str(tenant_id),
                "w": workspace_id,
                "actor": requested_by,
                # Same value as :t, textually — a separate bind name because
                # asyncpg infers one type per parameter name and tenant_id
                # (uuid) and resource_id (text) disagree on it.
                "resource_id": str(tenant_id),
            },
        )

    async def claim_requested(self) -> list[UUID]:
        """Atomically move every claimable row to `'exporting'` and return the
        tenants claimed. `UPDATE ... RETURNING` rather than `SELECT` then
        `UPDATE`: two worker instances polling at once must not both claim the
        same request, and a single statement's row locks are what makes that
        true without an explicit lock clause.

        Claimable is `status='requested'` (new work) OR `status IN
        ('exporting','purging')` gone stale (`updated_at` older than the
        window below) — a worker that claimed a request and then died leaves
        it stuck there forever otherwise, since nothing else ever moves a
        request off `'requested'`. Safe to resume from either status: export
        only reads and overwrites its own key, purge deletes rows that may
        already be gone (a second `DELETE` of nothing is not an error), and
        `mark_status` overwrites the same row either way — every step this
        claim unblocks is naturally idempotent, so re-running one from the
        start is correct, not just tolerated.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            rows = await session.execute(
                text(
                    "UPDATE platform.tenant_offboarding_requests"
                    " SET status = 'exporting', updated_at = now()"
                    " WHERE status = 'requested'"
                    "    OR (status IN ('exporting', 'purging')"
                    "        AND updated_at < now() - make_interval(mins => :stale_minutes))"
                    " RETURNING tenant_id"
                ),
                {"stale_minutes": _STALE_CLAIM_MINUTES},
            )
            return [row.tenant_id for row in rows]

    async def mark_status(
        self,
        tenant_id: UUID,
        status: str,
        *,
        export_key: str | None = None,
        error: str | None = None,
    ) -> None:
        """Report progress back on the tenant's own request row. Runs under
        `app.tenant_id`, not the drain — by now the caller knows exactly which
        tenant this is, and every write from here on is an ordinary
        tenant-scoped one.
        """
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_TENANT, {"tenant_id": str(tenant_id)})
            await session.execute(
                text(
                    "UPDATE platform.tenant_offboarding_requests"
                    " SET status = :status,"
                    "     export_key = coalesce(:export_key, export_key),"
                    "     error = :error,"
                    "     updated_at = now()"
                    " WHERE tenant_id = :tenant_id"
                ),
                {
                    "status": status,
                    "export_key": export_key,
                    "error": error,
                    "tenant_id": str(tenant_id),
                },
            )
