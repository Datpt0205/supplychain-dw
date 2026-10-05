"""Create the signal analyst's read-only login role. Idempotent, admin-only.

The role is the outermost of the four locks on `signal.query`: it holds
`GRANT SELECT` on three curated views and nothing else, so a query that escapes
every application-level guard still cannot reach a base table or a write verb.

This lives outside Alembic on purpose. A role is a cluster object, not a
database one, and `dw_migrator` runs migrations with `rolcreaterole = false` -
`CREATE ROLE` from a migration raises "permission denied to create role". Fresh
clusters get the role from `infra/compose/init/init-databases.sh`; this script is
for a cluster that was initialised before the role existed, which is every
developer machine and every deployed environment today.

Migration 0054 grants the role its SELECT privileges, guarded by `IF EXISTS`, so
the order of the two does not matter: run this first and 0054 grants on the way
past, run it second and re-running `alembic upgrade head` is a no-op that still
has to be followed by this script's own grants. Running this after 0054 applies
them directly, which is why the grants are repeated here.

Usage:
    uv run python scripts/create_agent_role.py
    uv run python scripts/create_agent_role.py --admin-url postgresql+asyncpg://...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = "dw_agent_ro"
DATABASE = "dw"

# The three views the analyst may read. Anything not listed here is unreachable
# for this role, which is the point of having it at all.
VIEWS = (
    "sales_intel.v_signal_tenders",
    "sales_intel.v_signal_bidders",
    "sales_intel.v_signal_accounts",
)


def _admin_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    url = os.environ.get("DW_ADMIN_DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("DW_DB_HOST", "127.0.0.1")
    # The host port this checkout publishes Postgres on (.env), not 5432: that
    # can be another checkout's database on the same machine.
    port = os.environ.get("DW_DB_PORT") or os.environ.get("DW_POSTGRES_HOST_PORT", "5432")
    if not user or not password:
        raise SystemExit(
            "Set DW_ADMIN_DATABASE_URL, or POSTGRES_USER and POSTGRES_PASSWORD, "
            "or pass --admin-url. Creating a role needs an account with CREATEROLE; "
            "dw_migrator does not have it."
        )
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/postgres"


def _password() -> str:
    password = os.environ.get("DW_DB_AGENT_RO_PASSWORD")
    if not password:
        raise SystemExit(
            "DW_DB_AGENT_RO_PASSWORD is not set. Add it to .env with the value the "
            "API will use in DW_AGENT_RO_DATABASE_URL - the two must match or "
            "signal.query cannot connect."
        )
    return password


def _literal(value: str) -> str:
    """A SQL string literal. `CREATE ROLE` is a utility statement and takes no
    bind parameters, so the password has to be inlined; doubling the quotes is
    what makes an apostrophe in it survive rather than end the literal early."""
    if "\\" in value:
        raise SystemExit(
            "DW_DB_AGENT_RO_PASSWORD contains a backslash. Postgres reads it "
            "literally here but not everywhere, so pick a password without one."
        )
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


async def _create_role(admin_url: str, password: str) -> str:
    """Create or re-password the role. Returns what happened, for the log."""
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                sa.text("SELECT true FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
            )
            # The password is set on both paths so a rotated .env takes effect
            # without anybody having to know whether the role already existed.
            verb = "ALTER" if exists else "CREATE"
            await conn.execute(
                sa.text(
                    f"{verb} ROLE {ROLE} LOGIN PASSWORD {_literal(password)} "
                    "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
                )
            )
            await conn.execute(sa.text(f"GRANT CONNECT ON DATABASE {DATABASE} TO {ROLE}"))
            return "created" if not exists else "updated"
    finally:
        await engine.dispose()


# The views are `security_invoker`, so Postgres checks the caller's rights
# against the base tables. Reading the required columns back out of the catalogue
# rather than listing them here means this script cannot drift from whatever the
# views currently select - and a column no view reads never gets granted.
_BASE_COLUMNS = """
SELECT table_schema, table_name, array_agg(DISTINCT column_name ORDER BY column_name)
FROM information_schema.view_column_usage
WHERE view_schema = 'sales_intel' AND view_name = ANY(:views)
GROUP BY table_schema, table_name
"""


async def _grant_views(admin_url: str) -> list[str]:
    """Grant SELECT on whichever views exist. Returns the ones that did not."""
    url = admin_url.rsplit("/", 1)[0] + f"/{DATABASE}"
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    missing: list[str] = []
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(f"GRANT USAGE ON SCHEMA sales_intel, sales_crm TO {ROLE}"))
            for view in VIEWS:
                present = await conn.scalar(
                    sa.text("SELECT to_regclass(:v) IS NOT NULL"), {"v": view}
                )
                if not present:
                    missing.append(view)
                    continue
                await conn.execute(sa.text(f"GRANT SELECT ON {view} TO {ROLE}"))
            if missing:
                return missing
            names = [v.split(".", 1)[1] for v in VIEWS]
            rows = (await conn.execute(sa.text(_BASE_COLUMNS), {"views": names})).fetchall()
            for schema, table, columns in rows:
                await conn.execute(
                    sa.text(f"GRANT SELECT ({', '.join(columns)}) ON {schema}.{table} TO {ROLE}")
                )
                print(f"  GRANT SELECT ({len(columns)} columns) ON {schema}.{table}")
    finally:
        await engine.dispose()
    return missing


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", default=None, help="SQLAlchemy URL for a CREATEROLE account")
    args = parser.parse_args()

    admin_url = _admin_url(args.admin_url)
    outcome = await _create_role(admin_url, _password())
    missing = await _grant_views(admin_url)

    print(f"role {ROLE}: {outcome}")
    for view in VIEWS:
        if view not in missing:
            print(f"  GRANT SELECT ON {view}")
    if missing:
        print(
            "\nThese views do not exist yet, so no grant was made:\n  "
            + "\n  ".join(missing)
            + "\n\nRun `make migrate` to apply 0054, then run this script again."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
