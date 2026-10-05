"""Supply Chain integration harness: its own database.

Named distinctly from `dw_platform`'s `pg_harness` and `dw_agent_runtime`'s
`runtime_harness` on purpose: neither directory is on the global `pythonpath`
in the root `pyproject.toml`, so each is importable as a bare module name
only via pytest's own per-directory path insertion. Reusing `pg_harness`
here would work in isolation but collide the moment a single pytest session
collects both packages' integration tests — whichever loads first wins
`sys.modules["pg_harness"]`, and the other's tests fail on an
`ImportError` for a name that file never defined.
"""

from __future__ import annotations

from pg_test_db import REPO_ROOT, DatabaseUrls, database_urls, recreate_database, run_alembic

__all__ = ["REPO_ROOT", "DatabaseUrls", "database_urls", "recreate_database", "run_alembic"]

TEST_DB = "dw_test_supply_chain"


def supply_chain_urls() -> DatabaseUrls:
    return database_urls(TEST_DB)
