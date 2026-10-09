"""Unit: `scripts/model_gate.py` (ticket ai-automation/06). Mock-safe by
construction: no test here builds a model provider or calls an endpoint, and a
mock run's result is marked so and is never evidence for a route."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "model_gate_script", REPO_ROOT / "scripts" / "model_gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["model_gate_script"] = module
    spec.loader.exec_module(module)
    return module


def _no_provider(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a gate test must not build a model provider")


def test_a_mock_run_writes_a_table_and_a_result_that_is_not_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import dw_agent_runtime.adapters.model_stack as model_stack

    monkeypatch.setattr(model_stack, "build_model_adapters", _no_provider)
    assert _script().main(["--profile", "qwen", "--mock", "--out", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "qwen.gate.json").read_text(encoding="utf-8"))
    assert result["mode"] == "mock" and result["profile"] == "qwen"
    assert set(result["tasks"]) == {
        "extract.sample_evaluation",
        "extract.supplier_confirmation_email",
        "extract.product_profile_bm04",
        "extract.supplier_quotation",
    }
    assert all(t["security_failed"] == 0 for t in result["tasks"].values())
    table = capsys.readouterr().out
    assert "| extract.sample_evaluation |" in table and "mock: not evidence" in table


def test_a_live_run_without_a_provider_in_the_environment_stops_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_BASE_URL"):
        _script().main(["--profile", "qwen", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []


def test_the_placeholder_qwen_profile_loads_and_no_host_defaults_to_it() -> None:
    from dw_agent_runtime.model.profiles import ModelProfileRegistry

    profiles = ModelProfileRegistry()
    profiles.load_directory(REPO_ROOT / "configs" / "models")
    assert profiles.resolve("qwen").profile_id == "qwen"
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MODEL_PROFILE=qwen" not in env_example
