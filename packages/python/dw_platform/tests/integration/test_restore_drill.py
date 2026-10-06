"""Restore drill: proves dump -> restore -> migrate-heads actually round-trips.

Before this test existed, the restore procedure lived only as a comment in
scripts/backup_postgres.sh, never run. `configs/policies/retention@1.6.0.yaml`
gated DROP PARTITION on exactly this: a rehearsal that actually happened, not
one written and never tried. It now does (`audit.enforced: true`), because
this test ran and passed.

Runs `pg_dump`/`pg_restore` INSIDE the Postgres container via `docker exec` -
the same way scripts/backup_postgres.sh and scripts/restore_postgres.sh do in
production - rather than a logical row-copy through SQLAlchemy, which would
prove a different, weaker claim (that the ORM can move rows, not that the
actual backup mechanism works). It also sidesteps needing a host-installed
`pg_dump`/`pg_restore` whose major version happens to match the server's.

Scope this drill does NOT cover: restoring onto a brand-new host/cluster. The
dump carries no CREATE ROLE statements - GRANTs inside it resolve against
roles that already exist in the *same* running cluster. A from-scratch
disaster recovery also needs the role-provisioning scripts
(scripts/create_agent_role.py, create_provisioner_role.py) to have run first;
that is a different rehearsal from this one.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import sqlalchemy as sa
from pg_harness import TEST_DB, DatabaseUrls, postgres_container, recreate_database, run_alembic
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

RESTORE_DB = "dw_test_restore_drill"
CONTAINER = postgres_container()
PG_USER = os.environ.get("PG_USER", "dw_admin")


def _docker_exec(
    *args: str, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    cmd = ["docker", "exec"]
    if input_bytes is not None:
        cmd.append("-i")
    cmd += [CONTAINER, *args]
    return subprocess.run(cmd, input=input_bytes, capture_output=True, check=False)


def _same_server_url(url: str, database: str) -> str:
    return url.rsplit("/", 1)[0] + f"/{database}"


async def test_dump_restore_round_trips_through_a_migrated_schema(
    db_urls: DatabaseUrls,
) -> None:
    marker = f"restore-drill-{uuid.uuid4().hex[:8]}"
    seed_engine = create_async_engine(db_urls.migrator, poolclass=NullPool)
    try:
        async with seed_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO platform.tenants (id, slug, name)"
                    " VALUES (gen_random_uuid(), :slug, :name)"
                ),
                {"slug": marker, "name": marker},
            )
    finally:
        await seed_engine.dispose()

    dump = _docker_exec("pg_dump", "-U", PG_USER, "-Fc", TEST_DB)
    assert dump.returncode == 0, dump.stderr.decode()
    assert dump.stdout, "pg_dump produced an empty archive"

    await recreate_database(db_urls.admin, RESTORE_DB)

    restore = _docker_exec(
        "pg_restore",
        "-U",
        PG_USER,
        "-d",
        RESTORE_DB,
        "--clean",
        "--if-exists",
        input_bytes=dump.stdout,
    )
    assert restore.returncode == 0, restore.stderr.decode()

    heads = run_alembic(["heads"], _same_server_url(db_urls.migrator, RESTORE_DB))
    assert heads.returncode == 0, f"{heads.stdout}\n{heads.stderr}"
    assert heads.stdout.count("(head)") == 1, (
        f"expected exactly one alembic head after restore, got:\n{heads.stdout}"
    )

    verify_engine = create_async_engine(
        _same_server_url(db_urls.admin, RESTORE_DB), poolclass=NullPool
    )
    try:
        async with verify_engine.connect() as conn:
            found = await conn.scalar(
                sa.text("SELECT count(*) FROM platform.tenants WHERE slug = :slug"),
                {"slug": marker},
            )
    finally:
        await verify_engine.dispose()
    assert found == 1, "seeded row did not survive the dump/restore round-trip"
