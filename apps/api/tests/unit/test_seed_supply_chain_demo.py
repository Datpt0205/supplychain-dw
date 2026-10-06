"""scripts/seed_supply_chain_demo.py refuses every profile but an explicit `local`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_script() -> ModuleType:  # scripts/ is not on the path; load by file
    path = REPO_ROOT / "scripts" / "seed_supply_chain_demo.py"
    spec = importlib.util.spec_from_file_location("seed_supply_chain_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_supply_chain_demo"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both URLs present, as in a shell holding a deployed database's .env.
    monkeypatch.setenv("DW_DATABASE_URL", "postgresql+asyncpg://m:p@db.example:5432/dw")
    monkeypatch.setenv("DW_API_DATABASE_URL", "postgresql+asyncpg://a:p@db.example:5432/dw")


@pytest.mark.usefixtures("database_urls")
@pytest.mark.parametrize("profile", [None, "", "test", "uat", "production"])
def test_anything_but_an_explicit_local_profile_refuses(
    monkeypatch: pytest.MonkeyPatch, profile: str | None
) -> None:
    if profile is None:
        monkeypatch.delenv("DW_API_PROFILE", raising=False)
    else:
        monkeypatch.setenv("DW_API_PROFILE", profile)

    with pytest.raises(SystemExit) as refused:
        _load_script()._urls()

    assert "refusing" in str(refused.value.code)


@pytest.mark.usefixtures("database_urls")
def test_the_local_profile_gets_the_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DW_API_PROFILE", "local")

    migrator, app = _load_script()._urls()

    assert migrator.startswith("postgresql+asyncpg://m:")
    assert app.startswith("postgresql+asyncpg://a:")


def test_the_stage_one_personas_can_take_steps_one_to_six() -> None:
    """R&D tests and passes samples; BGĐ holds both scopes `decide` asks for
    on a BGĐ review: `sc_bod` (the stamped scope) and a platform approval role
    (`approvals.decide`). Neither persona is the other."""
    personas = {subject: roles for subject, _, _, _, roles in _load_script().PERSONAS}
    assert personas["dev|linh.phan"] == ["member", "sc_rnd"]
    assert set(personas["dev|khanh.ngo"]) == {"approver", "sc_bod"}
