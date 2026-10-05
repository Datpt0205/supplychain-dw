"""Integration fixtures: disposable migrated database on local Postgres.

Requires `make infra-up`. Fails loudly when the database is unreachable —
integration tests never silently skip.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from supply_chain_harness import (
    TEST_DB,
    DatabaseUrls,
    recreate_database,
    run_alembic,
    supply_chain_urls,
)


@pytest.fixture(scope="session")
def db_urls() -> DatabaseUrls:
    urls = supply_chain_urls()
    try:
        asyncio.run(recreate_database(urls.admin, TEST_DB))
    except (OSError, OperationalError, InterfaceError) as exc:
        pytest.fail(
            f"Postgres unreachable at {urls.admin!r} — run `make infra-up` first. Error: {exc}"
        )
    result = run_alembic(["upgrade", "head"], urls.migrator)
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return urls
