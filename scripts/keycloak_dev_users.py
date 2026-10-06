"""Give every seeded dev user a Keycloak login, for a local browser run.

Usage (reads `.env`; in a plain shell run `set -a; source .env; set +a` first):
    uv run python scripts/keycloak_dev_users.py

For each user in the dev database that has an email, it creates the Keycloak
account in the app's realm if there is none with that email, then sets it
enabled, email-verified, and gives it the password in `DW_DEV_USER_PASSWORD`
(from `.env`, never committed). The app links a Keycloak identity to the
seeded user by email (`identity_provisioning.py`), so a login lands on that
user with the roles the seed gave it.

The users are read from the database, not listed here: the seed owns who
exists; this only makes them able to sign in. That includes the stage-1
personas `scripts/seed_supply_chain_demo.py seed` creates (Linh, R&D; Khánh,
BGĐ), so run the seed first. Refuses outside the `local` profile.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set (source .env)")
    return value


async def _seeded_users(database_url: str) -> list[tuple[str, str]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT email, display_name FROM platform.users"
                        " WHERE email IS NOT NULL ORDER BY email"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return [(row.email, row.display_name) for row in rows]


def _names(display_name: str) -> dict[str, str]:
    # Vietnamese order: family name first, given name last.
    *family, given = display_name.split()
    return {"firstName": given, "lastName": " ".join(family) or given}


def main() -> None:
    if os.environ.get("DW_API_PROFILE", "local") != "local":
        sys.exit("refusing: dev accounts with a shared password are for the local profile only")
    base = _env("NEXT_PUBLIC_KEYCLOAK_URL").rstrip("/")
    realm = _env("NEXT_PUBLIC_KEYCLOAK_REALM")
    password = _env("DW_DEV_USER_PASSWORD")
    users = asyncio.run(_seeded_users(_env("DW_DATABASE_URL")))
    if not users:
        sys.exit("no seeded users; seed the dev database first")

    with httpx.Client(base_url=base, timeout=30) as client:
        token = client.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": _env("KEYCLOAK_ADMIN"),
                "password": _env("KEYCLOAK_ADMIN_PASSWORD"),
            },
        )
        token.raise_for_status()
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        admin = f"/admin/realms/{realm}/users"

        for email, display_name in users:
            found = client.get(admin, params={"email": email, "exact": "true"})
            found.raise_for_status()
            profile = {
                "email": email,
                "enabled": True,
                "emailVerified": True,
                **_names(display_name),
            }
            if found.json():
                user_id = found.json()[0]["id"]
                client.put(f"{admin}/{user_id}", json=profile).raise_for_status()
                action = "updated"
            else:
                created = client.post(admin, json={"username": email.split("@", 1)[0], **profile})
                created.raise_for_status()
                user_id = created.headers["Location"].rsplit("/", 1)[-1]
                action = "created"
            client.put(
                f"{admin}/{user_id}/reset-password",
                json={"type": "password", "value": password, "temporary": False},
            ).raise_for_status()
            print(f"{email}: {action}, password set")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
