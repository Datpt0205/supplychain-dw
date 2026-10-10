"""A migrated database of the worker's own, for tests that run a whole lane.

Its own name, not the runtime's `dw_test_runtime`: a session fixture recreates
the database it owns, and two suites recreating one name in a single pytest
run would drop each other's rows mid-session.
"""

from __future__ import annotations

import asyncio

import pytest
from pg_test_db import DatabaseUrls, database_urls, recreate_database, run_migrations
from sqlalchemy.exc import InterfaceError, OperationalError

TEST_DB = "dw_test_worker"


@pytest.fixture(scope="session")
def worker_db() -> DatabaseUrls:
    urls = database_urls(TEST_DB)
    try:
        asyncio.run(recreate_database(urls.admin, TEST_DB))
    except (OSError, OperationalError, InterfaceError) as exc:
        pytest.fail(f"Postgres unreachable — run `make infra-up` first. Error: {exc}")
    result = run_migrations(urls.migrator)
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
    return urls
