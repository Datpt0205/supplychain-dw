"""Unit: compose hands the case-documents bucket name to all three readers.

`s3-setup` creates the bucket, the API writes to it, and the worker's
offboarding lane lists it for every tenant. Listing a bucket that does not
exist fails, so a name that reaches only some of them blocks every tenant's
offboarding. One variable, the same expression in all three services.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_COMPOSE = Path(__file__).resolve().parents[4] / "infra" / "compose" / "docker-compose.yml"
_VARIABLE = "CASE_DOCUMENTS_BUCKET"


def test_the_bucket_job_the_api_and_the_worker_read_one_name() -> None:
    services = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]

    given = {
        name: services[name]["environment"].get(_VARIABLE) for name in ("s3-setup", "api", "worker")
    }

    assert given["s3-setup"] is not None
    assert set(given.values()) == {given["s3-setup"]}, given
    assert f"rclone mkdir s3:$${{{_VARIABLE}}}" in services["s3-setup"]["command"][0]
