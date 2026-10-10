"""Create the provisioning login role ``dw_provisioner``. Idempotent, admin-only.

ADR-002. This role is the only cross-tenant write authority the API uses. It has
BYPASSRLS (it must create and list every tenant), but its grants reach ONLY the
platform provisioning tables — never a business schema — so it cannot read a
tenant's data. Which grants, exactly, has one owner: the database function
``platform.grant_provisioner_privileges()`` (migration f38f027d8342), which the
migrations call when the role exists. This script creates the role and calls the
same function, so the two can run in either order and never hold two lists.

A role is a cluster object and ``dw_migrator`` has no CREATEROLE, so a migration
cannot make it. Fresh clusters get it from
``infra/compose/init/init-databases.sh``; this script is for a cluster that was
initialised before the role existed — every environment deployed today.

Usage:
    uv run python scripts/create_provisioner_role.py
    uv run python scripts/create_provisioner_role.py --admin-url postgresql+asyncpg://...
"""

from __future__ import annotations

import argparse
import asyncio
import os

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = "dw_provisioner"
DATABASE = "dw"


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
            "or pass --admin-url. Creating a role needs an account with CREATEROLE."
        )
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/postgres"


def _password() -> str:
    password = os.environ.get("DW_DB_PROVISIONER_PASSWORD")
    if not password:
        raise SystemExit(
            "DW_DB_PROVISIONER_PASSWORD is not set. Add it to .env with the value "
            "the API will use in DW_PROVISIONER_DATABASE_URL — the two must match."
        )
    return password


def _literal(value: str) -> str:
    if "\\" in value:
        raise SystemExit("DW_DB_PROVISIONER_PASSWORD contains a backslash; pick one without.")
    return "'" + value.replace("'", "''") + "'"


async def _create_role(admin_url: str, password: str) -> str:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                sa.text("SELECT true FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
            )
            verb = "ALTER" if exists else "CREATE"
            await conn.execute(
                sa.text(
                    f"{verb} ROLE {ROLE} LOGIN PASSWORD {_literal(password)} "
                    "NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE"
                )
            )
            await conn.execute(sa.text(f"GRANT CONNECT ON DATABASE {DATABASE} TO {ROLE}"))
            return "created" if not exists else "updated"
    finally:
        await engine.dispose()


async def _grant(admin_url: str) -> None:
    """The grants the migrations give the role, from their one owner."""
    url = admin_url.rsplit("/", 1)[0] + f"/{DATABASE}"
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            defined = await conn.scalar(
                sa.text("SELECT to_regprocedure('platform.grant_provisioner_privileges()')")
            )
            if defined is None:
                raise SystemExit(
                    "platform.grant_provisioner_privileges() is missing: run the migrations "
                    "(make db-migrate), then this script again."
                )
            await conn.execute(sa.text("SELECT platform.grant_provisioner_privileges()"))
    finally:
        await engine.dispose()


async def _main(admin_url: str) -> None:
    what = await _create_role(admin_url, _password())
    print(f"role {ROLE}: {what}")
    await _grant(admin_url)
    print(f"grants applied for {ROLE} (provisioning tables only)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create dw_provisioner (idempotent).")
    parser.add_argument("--admin-url", default=None)
    args = parser.parse_args()
    asyncio.run(_main(_admin_url(args.admin_url)))


if __name__ == "__main__":
    main()
